/*
 * Training Length / Prefetch Degree / Wait Sensitivity Probe
 *
 * Characterizes fundamental AMP L2 prefetcher behavior in isolation
 * (no eviction pressure). Three modes:
 *
 *   train-length:     Sweep training accesses 1..32, measure distance=1
 *                     beyond training. Finds minimum training before
 *                     the prefetcher activates.
 *
 *   prefetch-degree:  Fix training=16, sweep measurement distance 1..64
 *                     beyond training. Reveals how far ahead the
 *                     prefetcher fetches.
 *
 *   wait-sensitivity: Fix training=16, distance=1, sweep wait time
 *                     100..10000 cycles. Reveals prefetcher response
 *                     latency.
 *
 * This benchmark should be run FIRST to validate that the prefetcher
 * is active and to calibrate parameters for the table-size benchmarks.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <getopt.h>
#include "../common/memory.h"
#include "../common/calibrate.h"

#define DEFAULT_REPS      5000
#define DEFAULT_WAIT      1000   /* cycles */

/* train-length mode */
#define TL_MAX_TRAIN      32
#define TL_MEASURE_DIST   1      /* measure 1 line beyond training */

/* prefetch-degree mode */
#define PD_TRAIN_LEN      16
#define PD_MAX_DISTANCE   64

/* wait-sensitivity mode */
#define WS_TRAIN_LEN      16
#define WS_MEASURE_DIST   1
#define WS_WAIT_MIN       100
#define WS_WAIT_MAX       10000
#define WS_WAIT_STEP      100

/* Enough lines for any mode */
#define MAX_LINES         128

enum mode { MODE_TRAIN_LENGTH, MODE_PREFETCH_DEGREE, MODE_WAIT_SENSITIVITY };

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s [options]\n"
        "  -c, --core N     CPU core to pin to (default: 0)\n"
        "  -r, --reps N     Repetitions per sweep point (default: %d)\n"
        "  -m, --mode MODE  'train-length', 'prefetch-degree', or\n"
        "                   'wait-sensitivity' (default: train-length)\n"
        "  -h, --help       Show this help\n",
        prog, DEFAULT_REPS);
    exit(1);
}

int main(int argc, char *argv[])
{
    int core_id = 0;
    int reps = DEFAULT_REPS;
    enum mode test_mode = MODE_TRAIN_LENGTH;

    static struct option long_opts[] = {
        {"core", required_argument, 0, 'c'},
        {"reps", required_argument, 0, 'r'},
        {"mode", required_argument, 0, 'm'},
        {"help", no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };
    int opt;
    while ((opt = getopt_long(argc, argv, "c:r:m:h", long_opts, NULL)) != -1) {
        switch (opt) {
        case 'c': core_id = atoi(optarg); break;
        case 'r': reps = atoi(optarg); break;
        case 'm':
            if (strcmp(optarg, "train-length") == 0)
                test_mode = MODE_TRAIN_LENGTH;
            else if (strcmp(optarg, "prefetch-degree") == 0)
                test_mode = MODE_PREFETCH_DEGREE;
            else if (strcmp(optarg, "wait-sensitivity") == 0)
                test_mode = MODE_WAIT_SENSITIVITY;
            else
                usage(argv[0]);
            break;
        default: usage(argv[0]);
        }
    }

    pin_to_core(core_id);

    size_t buf_size = MAX_LINES * CACHELINE_SIZE + PAGE_SIZE;
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

    uint64_t *samples = malloc(reps * sizeof(uint64_t));

    if (test_mode == MODE_TRAIN_LENGTH) {
        printf("# test: training_length_train\n");
        printf("# train_accesses,median,p5,p95,min,max,hits,misses,hit_rate\n");

        for (int train_len = 1; train_len <= TL_MAX_TRAIN; train_len++) {
            int measure_line = train_len + TL_MEASURE_DIST;

            for (int rep = 0; rep < reps; rep++) {
                /* FLUSH */
                for (int l = 0; l < measure_line + 4; l++)
                    clflush(buffer + (size_t)l * CACHELINE_SIZE);
                mfence();

                /* TRAIN: stride-1 */
                for (int l = 0; l < train_len; l++) {
                    compiler_barrier();
                    force_read(buffer + (size_t)l * CACHELINE_SIZE);
                }
                compiler_barrier();

                /* WAIT */
                delay_cycles(DEFAULT_WAIT);

                /* MEASURE */
                samples[rep] = timed_load(buffer + (size_t)measure_line * CACHELINE_SIZE);
            }

            measurement_stats_t stats = compute_stats(samples, reps, prof.hit_miss_threshold);
            print_csv_row(stdout, train_len, &stats, reps);
            fflush(stdout);
        }
    }
    else if (test_mode == MODE_PREFETCH_DEGREE) {
        printf("# test: training_length_degree\n");
        printf("# distance,median,p5,p95,min,max,hits,misses,hit_rate\n");

        for (int dist = 1; dist <= PD_MAX_DISTANCE; dist++) {
            int measure_line = PD_TRAIN_LEN + dist;

            for (int rep = 0; rep < reps; rep++) {
                /* FLUSH */
                for (int l = 0; l < measure_line + 4; l++)
                    clflush(buffer + (size_t)l * CACHELINE_SIZE);
                mfence();

                /* TRAIN: stride-1, fixed length */
                for (int l = 0; l < PD_TRAIN_LEN; l++) {
                    compiler_barrier();
                    force_read(buffer + (size_t)l * CACHELINE_SIZE);
                }
                compiler_barrier();

                /* WAIT */
                delay_cycles(DEFAULT_WAIT);

                /* MEASURE */
                samples[rep] = timed_load(buffer + (size_t)measure_line * CACHELINE_SIZE);
            }

            measurement_stats_t stats = compute_stats(samples, reps, prof.hit_miss_threshold);
            print_csv_row(stdout, dist, &stats, reps);
            fflush(stdout);
        }
    }
    else { /* MODE_WAIT_SENSITIVITY */
        printf("# test: training_length_wait\n");
        printf("# wait_cycles,median,p5,p95,min,max,hits,misses,hit_rate\n");

        int measure_line = WS_TRAIN_LEN + WS_MEASURE_DIST;

        for (int wait = WS_WAIT_MIN; wait <= WS_WAIT_MAX; wait += WS_WAIT_STEP) {
            for (int rep = 0; rep < reps; rep++) {
                /* FLUSH */
                for (int l = 0; l < measure_line + 4; l++)
                    clflush(buffer + (size_t)l * CACHELINE_SIZE);
                mfence();

                /* TRAIN */
                for (int l = 0; l < WS_TRAIN_LEN; l++) {
                    compiler_barrier();
                    force_read(buffer + (size_t)l * CACHELINE_SIZE);
                }
                compiler_barrier();

                /* WAIT (variable) */
                delay_cycles(wait);

                /* MEASURE */
                samples[rep] = timed_load(buffer + (size_t)measure_line * CACHELINE_SIZE);
            }

            measurement_stats_t stats = compute_stats(samples, reps, prof.hit_miss_threshold);
            print_csv_row(stdout, wait, &stats, reps);
            fflush(stdout);
        }
    }

    free(samples);
    free_hugepages((void *)buffer, buf_size);
    return 0;
}
