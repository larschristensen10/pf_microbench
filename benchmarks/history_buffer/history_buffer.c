/*
 * History Buffer Depth Benchmark
 *
 * Probes the depth of the L2 AMP prefetcher's history buffer -- the
 * record of recent demand accesses used for pattern detection.
 *
 * Methodology:
 *   1. Flush everything
 *   2. Train a stride-1 pattern: access lines 0, 1, 2, 3 in region A
 *   3. Inject N "poison" accesses on distinct pages (fills history buffer)
 *   4. Resume the stride-1 pattern: access lines 4, 5, 6, 7 in region A
 *   5. Wait for prefetcher
 *   6. Measure line 10 (beyond resume, only reachable by prefetch)
 *
 * If the history buffer is deep enough to retain the original pattern
 * after N poison accesses, the prefetcher resumes immediately on the
 * first resume access and runs far ahead. If the pattern was pushed out,
 * the prefetcher must re-learn from scratch during the resume, and may
 * not have run far enough ahead to reach line 10.
 *
 * Expected result: hit rate ~1.0 for N < buffer_depth, drop after.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <getopt.h>
#include "../common/memory.h"
#include "../common/calibrate.h"

#define TRAIN_LEN       4       /* initial training accesses */
#define RESUME_LEN      4       /* resume accesses after poison */
#define MEASURE_AHEAD   3       /* lines beyond resume to measure */
#define DEFAULT_REPS    5000
#define MAX_POISON      512
#define PREFETCH_WAIT   1000

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s [options]\n"
        "  -c, --core N        CPU core to pin to (default: 0)\n"
        "  -r, --reps N        Repetitions per sweep point (default: %d)\n"
        "  -n, --max-poison N  Max poison accesses (default: %d)\n"
        "  -h, --help          Show this help\n",
        prog, DEFAULT_REPS, MAX_POISON);
    exit(1);
}

/* Generate non-uniform sweep: dense near 0, sparser further out */
static int generate_sweep(int *out, int max_val)
{
    int count = 0;
    int n = 0;
    while (n <= max_val) {
        out[count++] = n;
        int step;
        if (n < 8) step = 1;
        else if (n < 32) step = 2;
        else if (n < 64) step = 4;
        else if (n < 128) step = 8;
        else if (n < 256) step = 16;
        else step = 32;
        n += step;
    }
    return count;
}

int main(int argc, char *argv[])
{
    int core_id = 0;
    int reps = DEFAULT_REPS;
    int max_poison = MAX_POISON;

    static struct option long_opts[] = {
        {"core",       required_argument, 0, 'c'},
        {"reps",       required_argument, 0, 'r'},
        {"max-poison", required_argument, 0, 'n'},
        {"help",       no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };
    int opt;
    while ((opt = getopt_long(argc, argv, "c:r:n:h", long_opts, NULL)) != -1) {
        switch (opt) {
        case 'c': core_id = atoi(optarg); break;
        case 'r': reps = atoi(optarg); break;
        case 'n': max_poison = atoi(optarg); break;
        default:  usage(argv[0]);
        }
    }

    pin_to_core(core_id);

    /*
     * Memory layout:
     *   - Region 0 (first page): pattern accesses (lines 0..~14)
     *   - Regions 1..max_poison: one poison access per page
     *     Each poison access at mid-page (line 32) to avoid spatial overlap
     */
    size_t n_pages = max_poison + 2;
    size_t buf_size = n_pages * PAGE_SIZE;
    /* Use regular pages, not hugepages: each poison access hits a distinct
     * 4K page by design, so hugepages provide no TLB benefit and the
     * allocation (just over 2MB) can straddle a hugepage boundary unreliably. */
    volatile char *buffer = (volatile char *)mmap(NULL, buf_size,
        PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (buffer == MAP_FAILED) {
        perror("mmap");
        return 1;
    }
    memset((void *)buffer, 0, buf_size);

    volatile char *pattern_base = buffer;
    int measure_line = TRAIN_LEN + RESUME_LEN + MEASURE_AHEAD;

    latency_profile_t prof = calibrate_latencies((void *)buffer);
    fprintf(stderr, "# calibration: L2_hit=%lu cycles, DRAM=%lu cycles, threshold=%lu cycles\n",
            (unsigned long)prof.l2_hit_median,
            (unsigned long)prof.dram_median,
            (unsigned long)prof.hit_miss_threshold);
    fprintf(stderr, "# measure_line=%d\n", measure_line);

    printf("# test: history_buffer\n");
    printf("# poison_accesses,median,p5,p95,min,max,hits,misses,hit_rate\n");

    uint64_t *samples = malloc(reps * sizeof(uint64_t));
    int sweep[1024];
    int sweep_len = generate_sweep(sweep, max_poison);

    for (int si = 0; si < sweep_len; si++) {
        int n_poison = sweep[si];

        for (int rep = 0; rep < reps; rep++) {
            /*
             * FLUSH: pattern region + poison pages
             */
            for (int l = 0; l <= measure_line + 2; l++) {
                clflush(pattern_base + (size_t)l * CACHELINE_SIZE);
            }
            for (int p = 0; p < n_poison; p++) {
                clflush(buffer + (size_t)(p + 1) * PAGE_SIZE + 32 * CACHELINE_SIZE);
            }
            mfence();

            /*
             * TRAIN: stride-1 pattern
             */
            for (int l = 0; l < TRAIN_LEN; l++) {
                compiler_barrier();
                force_read(pattern_base + (size_t)l * CACHELINE_SIZE);
                delay_cycles(TRAIN_DELAY);
            }
            compiler_barrier();

            /*
             * POISON: N unrelated accesses on distinct pages
             */
            for (int p = 0; p < n_poison; p++) {
                compiler_barrier();
                force_read(buffer + (size_t)(p + 1) * PAGE_SIZE + 32 * CACHELINE_SIZE);
                delay_cycles(TRAIN_DELAY);
            }
            compiler_barrier();

            /*
             * RESUME: continue the stride-1 pattern from where training left off
             */
            for (int l = TRAIN_LEN; l < TRAIN_LEN + RESUME_LEN; l++) {
                compiler_barrier();
                force_read(pattern_base + (size_t)l * CACHELINE_SIZE);
                delay_cycles(TRAIN_DELAY);
            }
            compiler_barrier();

            /* Wait for prefetcher */
            delay_cycles(PREFETCH_WAIT);

            /*
             * MEASURE: line beyond resume (only reachable by prefetch)
             */
            samples[rep] = timed_load(pattern_base + (size_t)measure_line * CACHELINE_SIZE);
        }

        measurement_stats_t stats = compute_stats(samples, reps, prof.hit_miss_threshold);
        print_csv_row(stdout, n_poison, &stats, reps);
        fflush(stdout);
    }

    free(samples);
    munmap((void *)buffer, buf_size);
    return 0;
}
