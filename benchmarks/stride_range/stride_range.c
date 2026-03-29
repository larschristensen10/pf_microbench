/*
 * Stride Detection Range Benchmark
 *
 * Tests which stride values (in cache lines) the L2 AMP prefetcher
 * can detect and prefetch.
 *
 * For each stride S:
 *   1. Flush ALL cache lines in the spanned range
 *   2. Train with 16 stride-S accesses (offsets 0, S, 2S, ..., 15S)
 *   3. Wait for prefetcher
 *   4. Measure at offset 16S (the next expected stride element)
 *
 * If the prefetcher detects stride S, offset 16S should be an L2 hit.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <getopt.h>
#include "../common/memory.h"
#include "../common/calibrate.h"

#define TRAIN_ACCESSES  16
#define DEFAULT_REPS    5000
#define PREFETCH_WAIT   1000

/* Strides to test (in cache lines) */
static const int STRIDES[] = {1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64};
#define N_STRIDES (int)(sizeof(STRIDES) / sizeof(STRIDES[0]))

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s [options]\n"
        "  -c, --core N     CPU core to pin to (default: 0)\n"
        "  -r, --reps N     Repetitions per stride (default: %d)\n"
        "  -h, --help       Show this help\n",
        prog, DEFAULT_REPS);
    exit(1);
}

int main(int argc, char *argv[])
{
    int core_id = 0;
    int reps = DEFAULT_REPS;

    static struct option long_opts[] = {
        {"core", required_argument, 0, 'c'},
        {"reps", required_argument, 0, 'r'},
        {"help", no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };
    int opt;
    while ((opt = getopt_long(argc, argv, "c:r:h", long_opts, NULL)) != -1) {
        switch (opt) {
        case 'c': core_id = atoi(optarg); break;
        case 'r': reps = atoi(optarg); break;
        default:  usage(argv[0]);
        }
    }

    pin_to_core(core_id);

    /* Max offset: (TRAIN_ACCESSES + 1) * max_stride * CACHELINE_SIZE
     * = 17 * 64 * 64 = 69632 bytes. Allocate generously. */
    size_t buf_size = (TRAIN_ACCESSES + 2) * STRIDES[N_STRIDES - 1] * CACHELINE_SIZE + PAGE_SIZE;
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

    printf("# test: stride_range\n");
    printf("# stride_cachelines,median,p5,p95,min,max,hits,misses,hit_rate\n");

    uint64_t *samples = malloc(reps * sizeof(uint64_t));

    for (int si = 0; si < N_STRIDES; si++) {
        int stride = STRIDES[si];
        /* Measurement line: one stride beyond the last training access */
        int measure_offset = TRAIN_ACCESSES * stride;
        /* Total lines spanned: from 0 to measure_offset (inclusive) */
        int total_lines = measure_offset + 1;

        for (int rep = 0; rep < reps; rep++) {
            /*
             * FLUSH: all cache lines in the spanned range,
             * not just stride-aligned ones, to prevent any
             * adjacent-line or spatial prefetch false positives.
             */
            for (int l = 0; l < total_lines; l++)
                clflush(buffer + (size_t)l * CACHELINE_SIZE);
            mfence();

            /*
             * TRAIN: stride-S pattern
             */
            for (int i = 0; i < TRAIN_ACCESSES; i++) {
                compiler_barrier();
                force_read(buffer + (size_t)(i * stride) * CACHELINE_SIZE);
                delay_cycles(TRAIN_DELAY);
            }
            compiler_barrier();

            /* WAIT */
            delay_cycles(PREFETCH_WAIT);

            /* MEASURE: next stride element */
            samples[rep] = timed_load(buffer + (size_t)measure_offset * CACHELINE_SIZE);
        }

        measurement_stats_t stats = compute_stats(samples, reps, prof.hit_miss_threshold);
        print_csv_row(stdout, stride, &stats, reps);
        fflush(stdout);
    }

    free(samples);
    free_hugepages((void *)buffer, buf_size);
    return 0;
}
