/*
 * IP-based Pattern Table Size Benchmark
 *
 * Probes whether the L2 AMP prefetcher uses the instruction pointer (IP)
 * to disambiguate streams, and if so, how many IPs it can track.
 *
 * Same structure as stream_tracker, but each stream's training and
 * resume loads go through a distinct code stub (mmap'd at unique addresses).
 * This ensures each stream is associated with a unique IP.
 *
 * Compare with stream_tracker results (single IP for all streams):
 *   - Same cliff → IP not a separate bottleneck
 *   - Different cliff → IP table is a separate structure
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <getopt.h>
#include <sys/mman.h>
#include "../common/memory.h"
#include "../common/calibrate.h"

#define INITIAL_TRAIN   4
#define EVICT_TRAIN     8
#define RESUME_LEN      4
#define MEASURE_AHEAD   4
#define STREAM_SPACING  (512 * 1024)
#define MAX_STUBS       128
#define DEFAULT_REPS    5000
#define PREFETCH_WAIT   1000
#define LINES_PER_STREAM 24

/*
 * Each stub: loads from address in %rdi, returns value in %rax.
 *   mov (%rdi), %rax    # 48 8b 07
 *   ret                  # c3
 * Placed on separate pages for unique IPs.
 */
static const unsigned char STUB_CODE[] = {
    0x48, 0x8b, 0x07,  /* mov (%rdi), %rax */
    0xc3                /* ret */
};

typedef uint64_t (*load_fn)(volatile void *addr);

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s [options]\n"
        "  -c, --core N        CPU core to pin to (default: 0)\n"
        "  -r, --reps N        Repetitions per sweep point (default: %d)\n"
        "  -n, --max-stubs N   Max IP stubs to test (default: %d)\n"
        "  -h, --help          Show this help\n",
        prog, DEFAULT_REPS, MAX_STUBS);
    exit(1);
}

int main(int argc, char *argv[])
{
    int core_id = 0;
    int reps = DEFAULT_REPS;
    int max_stubs = MAX_STUBS;

    static struct option long_opts[] = {
        {"core",      required_argument, 0, 'c'},
        {"reps",      required_argument, 0, 'r'},
        {"max-stubs", required_argument, 0, 'n'},
        {"help",      no_argument,       0, 'h'},
        {0, 0, 0, 0}
    };
    int opt;
    while ((opt = getopt_long(argc, argv, "c:r:n:h", long_opts, NULL)) != -1) {
        switch (opt) {
        case 'c': core_id = atoi(optarg); break;
        case 'r': reps = atoi(optarg); break;
        case 'n': max_stubs = atoi(optarg); break;
        default:  usage(argv[0]);
        }
    }

    pin_to_core(core_id);

    /* Allocate executable stubs, each on its own page for unique IP */
    load_fn *stubs = malloc(max_stubs * sizeof(load_fn));
    for (int i = 0; i < max_stubs; i++) {
        void *page = mmap(NULL, PAGE_SIZE,
                          PROT_READ | PROT_WRITE | PROT_EXEC,
                          MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (page == MAP_FAILED) {
            perror("mmap stub");
            return 1;
        }
        memcpy(page, STUB_CODE, sizeof(STUB_CODE));
        stubs[i] = (load_fn)page;
    }

    /* Data buffer */
    size_t buf_size = (size_t)max_stubs * STREAM_SPACING + PAGE_SIZE;
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

    int measure_line = INITIAL_TRAIN + RESUME_LEN + MEASURE_AHEAD;
    fprintf(stderr, "# measure_line=%d\n", measure_line);

    printf("# test: ip_table\n");
    printf("# stubs,median,p5,p95,min,max,hits,misses,hit_rate\n");

    uint64_t *samples = malloc(reps * sizeof(uint64_t));

    for (int n_stubs = 1; n_stubs <= max_stubs; n_stubs++) {
        for (int rep = 0; rep < reps; rep++) {
            /*
             * FLUSH
             */
            for (int s = 0; s < n_stubs; s++) {
                volatile char *base = buffer + (size_t)s * STREAM_SPACING;
                for (int l = 0; l < LINES_PER_STREAM; l++) {
                    clflush(base + (size_t)l * CACHELINE_SIZE);
                }
            }
            mfence();

            /*
             * TRAIN stream 0 (short) using stub 0
             */
            {
                volatile char *base = buffer;
                for (int l = 0; l < INITIAL_TRAIN; l++) {
                    compiler_barrier();
                    stubs[0](base + (size_t)l * CACHELINE_SIZE);
                    delay_cycles(TRAIN_DELAY);
                }
            }
            compiler_barrier();

            /*
             * TRAIN evictor streams 1..N-1, each using its own stub
             */
            for (int s = 1; s < n_stubs; s++) {
                volatile char *base = buffer + (size_t)s * STREAM_SPACING;
                for (int l = 0; l < EVICT_TRAIN; l++) {
                    compiler_barrier();
                    stubs[s](base + (size_t)l * CACHELINE_SIZE);
                    delay_cycles(TRAIN_DELAY);
                }
                compiler_barrier();
            }

            /*
             * RESUME stream 0 using stub 0
             */
            {
                volatile char *base = buffer;
                for (int l = INITIAL_TRAIN; l < INITIAL_TRAIN + RESUME_LEN; l++) {
                    compiler_barrier();
                    stubs[0](base + (size_t)l * CACHELINE_SIZE);
                    delay_cycles(TRAIN_DELAY);
                }
            }
            compiler_barrier();

            delay_cycles(PREFETCH_WAIT);

            /*
             * MEASURE (using generic timed_load, not a stub)
             */
            samples[rep] = timed_load(buffer + (size_t)measure_line * CACHELINE_SIZE);
        }

        measurement_stats_t stats = compute_stats(samples, reps, prof.hit_miss_threshold);
        print_csv_row(stdout, n_stubs, &stats, reps);
        fflush(stdout);
    }

    free(samples);
    free_hugepages((void *)buffer, buf_size);
    for (int i = 0; i < max_stubs; i++)
        munmap((void *)stubs[i], PAGE_SIZE);
    free(stubs);
    return 0;
}
