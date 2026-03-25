/*
 * Stream Tracker Table Size Benchmark
 *
 * Probes how many independent stride-1 streams the L2 AMP prefetcher
 * can track simultaneously.
 *
 * Methodology:
 *   1. Flush a wide range of cache lines for all N streams
 *   2. Train stream 0 first (short: 4 lines, so prefetcher starts but
 *      hasn't run far ahead)
 *   3. Train streams 1..N-1 (longer: 8 lines each, well-established)
 *   4. Resume stream 0 by accessing the next several lines
 *      (continuing the sequence from where training left off)
 *   5. Wait for prefetcher to run ahead
 *   6. Measure a line well beyond the resume point
 *
 * If stream 0 is still tracked (not evicted by streams 1..N-1),
 * the prefetcher runs ahead quickly and the measured line is an L2 hit.
 * If evicted, the prefetcher must re-learn the stride from scratch,
 * meaning fewer lines get prefetched by measurement time.
 *
 * Expected result: hit rate ~1.0 for N <= table_size, drop after.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <getopt.h>
#include "../common/memory.h"
#include "../common/calibrate.h"

#define INITIAL_TRAIN   4   /* short training for stream 0 (the target) */
#define EVICT_TRAIN     8   /* longer training for evictor streams */
#define RESUME_LEN      4   /* accesses when resuming stream 0 */
#define MEASURE_AHEAD   4   /* how many lines beyond resume to measure */
#define STREAM_SPACING  (512 * 1024)  /* 512KB between stream bases */
#define MAX_STREAMS     128
#define DEFAULT_REPS    5000
#define PREFETCH_WAIT   1000 /* cycles to wait for prefetcher after resume */

/* Total lines we touch per stream (for flushing) */
#define LINES_PER_STREAM 24

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s [options]\n"
        "  -c, --core N        CPU core to pin to (default: 0)\n"
        "  -r, --reps N        Repetitions per sweep point (default: %d)\n"
        "  -n, --max-streams N Max streams to test (default: %d)\n"
        "  -h, --help          Show this help\n",
        prog, DEFAULT_REPS, MAX_STREAMS);
    exit(1);
}

int main(int argc, char *argv[])
{
    int core_id = 0;
    int reps = DEFAULT_REPS;
    int max_streams = MAX_STREAMS;

    static struct option long_opts[] = {
        {"core",        required_argument, 0, 'c'},
        {"reps",        required_argument, 0, 'r'},
        {"max-streams", required_argument, 0, 'n'},
        {"help",        no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };
    int opt;
    while ((opt = getopt_long(argc, argv, "c:r:n:h", long_opts, NULL)) != -1) {
        switch (opt) {
        case 'c': core_id = atoi(optarg); break;
        case 'r': reps = atoi(optarg); break;
        case 'n': max_streams = atoi(optarg); break;
        default:  usage(argv[0]);
        }
    }

    pin_to_core(core_id);

    size_t buf_size = (size_t)max_streams * STREAM_SPACING + PAGE_SIZE;
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

    /* The line we'll measure is: INITIAL_TRAIN + RESUME_LEN + MEASURE_AHEAD */
    int measure_line = INITIAL_TRAIN + RESUME_LEN + MEASURE_AHEAD;
    fprintf(stderr, "# measure_line=%d (offset %d bytes from stream base)\n",
            measure_line, measure_line * CACHELINE_SIZE);

    printf("# test: stream_tracker\n");
    printf("# streams,median,p5,p95,min,max,hits,misses,hit_rate\n");

    uint64_t *samples = malloc(reps * sizeof(uint64_t));

    for (int n_streams = 1; n_streams <= max_streams; n_streams++) {
        for (int rep = 0; rep < reps; rep++) {
            /*
             * PHASE 1: FLUSH all lines for all streams
             */
            for (int s = 0; s < n_streams; s++) {
                volatile char *base = buffer + (size_t)s * STREAM_SPACING;
                for (int l = 0; l < LINES_PER_STREAM; l++) {
                    clflush(base + (size_t)l * CACHELINE_SIZE);
                }
            }
            mfence();

            /*
             * PHASE 2: TRAIN stream 0 (short -- just enough to start tracking)
             */
            {
                volatile char *base = buffer;
                for (int l = 0; l < INITIAL_TRAIN; l++) {
                    compiler_barrier();
                    force_read(base + (size_t)l * CACHELINE_SIZE);
                }
            }
            compiler_barrier();

            /*
             * PHASE 3: TRAIN evictor streams 1..N-1
             * Each gets longer training to firmly establish in the tracker.
             */
            for (int s = 1; s < n_streams; s++) {
                volatile char *base = buffer + (size_t)s * STREAM_SPACING;
                for (int l = 0; l < EVICT_TRAIN; l++) {
                    compiler_barrier();
                    force_read(base + (size_t)l * CACHELINE_SIZE);
                }
                compiler_barrier();
            }

            /*
             * PHASE 4: RESUME stream 0
             * Continue the stride-1 pattern. If stream 0 is still tracked,
             * the prefetcher immediately resumes prefetching ahead.
             * If evicted, it must re-detect the stride (~2-3 accesses).
             */
            {
                volatile char *base = buffer;
                for (int l = INITIAL_TRAIN; l < INITIAL_TRAIN + RESUME_LEN; l++) {
                    compiler_barrier();
                    force_read(base + (size_t)l * CACHELINE_SIZE);
                }
            }
            compiler_barrier();

            /*
             * PHASE 5: WAIT for prefetcher to complete fetches
             */
            delay_cycles(PREFETCH_WAIT);

            /*
             * PHASE 6: MEASURE
             * The measured line is MEASURE_AHEAD positions beyond the
             * last resumed access. It was never explicitly accessed.
             * If the prefetcher ran ahead, it's in L2. Otherwise, DRAM.
             */
            samples[rep] = timed_load(buffer + (size_t)measure_line * CACHELINE_SIZE);
        }

        measurement_stats_t stats = compute_stats(samples, reps, prof.hit_miss_threshold);
        print_csv_row(stdout, n_streams, &stats, reps);
        fflush(stdout);
    }

    free(samples);
    free_hugepages((void *)buffer, buf_size);
    return 0;
}
