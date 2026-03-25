/*
 * Spatial Region Table (Access Map) Size Benchmark
 *
 * Probes how many page-level spatial patterns the L2 AMP prefetcher
 * can simultaneously track.
 *
 * Methodology:
 *   On each of N pages, access a specific spatial pattern (offsets 0, 2, 5).
 *   This trains the prefetcher to associate this pattern with pages.
 *
 *   - "replay" mode: After training N pages, access a FRESH page at
 *     offset 0 (trigger). If the spatial pattern is still in the table,
 *     the prefetcher should proactively fetch offsets 2 and 5.
 *
 *   - "eviction" mode: Train on N pages, then test if the first page's
 *     pattern can still be replayed by accessing it on yet another fresh page.
 *     (The idea: if N exceeds the table, page 0's pattern is evicted.)
 *
 * Expected result: hit rate ~1.0 for N <= table_size, drop after.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <getopt.h>
#include "../common/memory.h"
#include "../common/calibrate.h"

/* Spatial pattern: cache line offsets within a 4KB page (page has 64 lines) */
static const int PATTERN[] = {0, 2, 5};
#define PATTERN_LEN     3
#define TRIGGER_OFFSET  0   /* cache line offset used as trigger on test page */
#define MEASURE_OFFSET  2   /* cache line offset to measure (should be prefetched) */
#define MEASURE2_OFFSET 5   /* second measurement for stronger signal */

#define MAX_PAGES       256
#define DEFAULT_REPS    5000
#define PREFETCH_WAIT   1000

/* We need pages that are spaced far enough apart so each is in a distinct
 * "spatial region" from the prefetcher's perspective. Using one 4KB page per
 * region, with 8KB spacing to avoid adjacent-page effects. */
#define REGION_SPACING  (8 * 1024)

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s [options]\n"
        "  -c, --core N       CPU core to pin to (default: 0)\n"
        "  -r, --reps N       Repetitions per sweep point (default: %d)\n"
        "  -n, --max-pages N  Max pages to test (default: %d)\n"
        "  -m, --mode MODE    'replay' or 'eviction' (default: replay)\n"
        "  -h, --help         Show this help\n",
        prog, DEFAULT_REPS, MAX_PAGES);
    exit(1);
}

enum mode { MODE_REPLAY, MODE_EVICTION };

int main(int argc, char *argv[])
{
    int core_id = 0;
    int reps = DEFAULT_REPS;
    int max_pages = MAX_PAGES;
    enum mode test_mode = MODE_REPLAY;

    static struct option long_opts[] = {
        {"core",      required_argument, 0, 'c'},
        {"reps",      required_argument, 0, 'r'},
        {"max-pages", required_argument, 0, 'n'},
        {"mode",      required_argument, 0, 'm'},
        {"help",      no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };
    int opt;
    while ((opt = getopt_long(argc, argv, "c:r:n:m:h", long_opts, NULL)) != -1) {
        switch (opt) {
        case 'c': core_id = atoi(optarg); break;
        case 'r': reps = atoi(optarg); break;
        case 'n': max_pages = atoi(optarg); break;
        case 'm':
            if (strcmp(optarg, "eviction") == 0)
                test_mode = MODE_EVICTION;
            else if (strcmp(optarg, "replay") == 0)
                test_mode = MODE_REPLAY;
            else
                usage(argv[0]);
            break;
        default: usage(argv[0]);
        }
    }

    pin_to_core(core_id);

    /* Need max_pages + 1 regions (extra for the test page) */
    size_t n_regions = max_pages + 2;
    size_t buf_size = n_regions * REGION_SPACING;
    volatile char *buffer = (volatile char *)alloc_hugepages(buf_size);
    if (buffer == MAP_FAILED) {
        perror("alloc_hugepages");
        return 1;
    }

    latency_profile_t prof = calibrate_latencies((void *)buffer);
    fprintf(stderr, "# calibration: L2_hit=%lu cycles, DRAM=%lu cycles, threshold=%lu cycles\n",
            (unsigned long)prof.l2_hit_median,
            (unsigned long)prof.dram_median,
            (unsigned long)prof.hit_miss_threshold);

    const char *mode_str = (test_mode == MODE_REPLAY) ? "replay" : "eviction";
    printf("# test: spatial_region_%s\n", mode_str);
    printf("# pages,median,p5,p95,min,max,hits,misses,hit_rate\n");

    uint64_t *samples = malloc(reps * sizeof(uint64_t));

    for (int n_pages = 1; n_pages <= max_pages; n_pages++) {
        for (int rep = 0; rep < reps; rep++) {
            /*
             * FLUSH: all pattern offsets on all training pages + test page
             */
            int total_pages = n_pages + 1; /* +1 for fresh test page */
            for (int p = 0; p < total_pages; p++) {
                volatile char *region = buffer + (size_t)p * REGION_SPACING;
                /* Flush all 64 lines in the page to start clean */
                for (int l = 0; l < 64; l++) {
                    clflush(region + (size_t)l * CACHELINE_SIZE);
                }
            }
            mfence();

            /*
             * TRAIN: access the spatial pattern on each of N pages.
             * Each page gets accesses at offsets 0, 2, 5 (in cache lines).
             * The prefetcher should learn: "when offset 0 is accessed,
             * also fetch offsets 2 and 5."
             */
            for (int p = 0; p < n_pages; p++) {
                volatile char *region = buffer + (size_t)p * REGION_SPACING;
                for (int i = 0; i < PATTERN_LEN; i++) {
                    compiler_barrier();
                    force_read(region + (size_t)PATTERN[i] * CACHELINE_SIZE);
                }
                compiler_barrier();
            }

            /*
             * TEST: access offset 0 (trigger) on a FRESH page that was
             * never part of training. The prefetcher should replicate the
             * learned spatial pattern and prefetch offsets 2 and 5.
             */
            volatile char *test_page = buffer + (size_t)n_pages * REGION_SPACING;

            compiler_barrier();
            force_read(test_page + (size_t)TRIGGER_OFFSET * CACHELINE_SIZE);
            compiler_barrier();

            /* Wait for spatial prefetch */
            delay_cycles(PREFETCH_WAIT);

            /*
             * MEASURE offset 2 on the test page
             */
            samples[rep] = timed_load(test_page + (size_t)MEASURE_OFFSET * CACHELINE_SIZE);
        }

        measurement_stats_t stats = compute_stats(samples, reps, prof.hit_miss_threshold);
        print_csv_row(stdout, n_pages, &stats, reps);
        fflush(stdout);
    }

    free(samples);
    free_hugepages((void *)buffer, buf_size);
    return 0;
}
