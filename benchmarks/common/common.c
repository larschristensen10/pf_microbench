#define _GNU_SOURCE
#include <sched.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include "memory.h"
#include "calibrate.h"

/* ---- sorting helper ---- */

static int cmp_u64(const void *a, const void *b)
{
    uint64_t va = *(const uint64_t *)a;
    uint64_t vb = *(const uint64_t *)b;
    return (va > vb) - (va < vb);
}

static uint64_t percentile_u64(uint64_t *sorted, int n, int pct)
{
    int idx = (int)((long)n * pct / 100);
    if (idx >= n) idx = n - 1;
    if (idx < 0) idx = 0;
    return sorted[idx];
}

/* ---- core pinning ---- */

void pin_to_core(int core_id)
{
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(core_id, &set);
    if (sched_setaffinity(0, sizeof(set), &set) != 0) {
        perror("sched_setaffinity");
        fprintf(stderr, "WARNING: could not pin to core %d\n", core_id);
    }
}

/* ---- calibration ---- */

#define CAL_REPS 2000
#define CAL_WARMUP 200

latency_profile_t calibrate_latencies(void *buffer)
{
    volatile char *line = (volatile char *)buffer;
    uint64_t samples[CAL_REPS];
    latency_profile_t prof;

    /* --- Measure L2 hit latency --- */
    /* Access the line to bring it into L1/L2, then repeatedly time it. */
    *line;  /* warm into cache */
    mfence();

    for (int i = 0; i < CAL_WARMUP; i++) {
        (void)timed_load((volatile void *)line);
    }
    for (int i = 0; i < CAL_REPS; i++) {
        /* Line is in L1d/L2 from previous access */
        samples[i] = timed_load((volatile void *)line);
    }
    qsort(samples, CAL_REPS, sizeof(uint64_t), cmp_u64);
    prof.l2_hit_median = samples[CAL_REPS / 2];

    /* --- Measure DRAM latency --- */
    /* Flush the line, then time the access. */
    for (int i = 0; i < CAL_WARMUP; i++) {
        clflush((volatile void *)line);
        mfence();
        (void)timed_load((volatile void *)line);
    }
    for (int i = 0; i < CAL_REPS; i++) {
        clflush((volatile void *)line);
        mfence();
        samples[i] = timed_load((volatile void *)line);
    }
    qsort(samples, CAL_REPS, sizeof(uint64_t), cmp_u64);
    prof.dram_median = samples[CAL_REPS / 2];

    /* Threshold: geometric mean of L2 hit and DRAM, biased toward L2.
     * In practice L2 ~5-12 cycles, DRAM ~150-300 cycles, so threshold
     * ends up around 30-60 cycles. Anything above = not prefetched into L2. */
    prof.hit_miss_threshold = (prof.l2_hit_median + prof.dram_median) / 3;
    if (prof.hit_miss_threshold < 30)
        prof.hit_miss_threshold = 30;

    return prof;
}

/* ---- statistics ---- */

measurement_stats_t compute_stats(uint64_t *samples, int n, uint64_t threshold)
{
    measurement_stats_t s;
    uint64_t *buf = malloc(n * sizeof(uint64_t));
    memcpy(buf, samples, n * sizeof(uint64_t));
    qsort(buf, n, sizeof(uint64_t), cmp_u64);

    s.median = buf[n / 2];
    s.p5     = percentile_u64(buf, n, 5);
    s.p95    = percentile_u64(buf, n, 95);
    s.min    = buf[0];
    s.max    = buf[n - 1];

    s.hit_count = 0;
    s.miss_count = 0;
    for (int i = 0; i < n; i++) {
        if (samples[i] < threshold)
            s.hit_count++;
        else
            s.miss_count++;
    }

    free(buf);
    return s;
}

/* ---- CSV output ---- */

void print_csv_header(FILE *out, const char *test_name)
{
    fprintf(out, "# test: %s\n", test_name);
}

void print_csv_row(FILE *out, int param_value, measurement_stats_t *stats, int reps)
{
    double hit_rate = (double)stats->hit_count / reps;
    fprintf(out, "%d,%lu,%lu,%lu,%lu,%lu,%d,%d,%.4f\n",
            param_value,
            (unsigned long)stats->median,
            (unsigned long)stats->p5,
            (unsigned long)stats->p95,
            (unsigned long)stats->min,
            (unsigned long)stats->max,
            stats->hit_count,
            stats->miss_count,
            hit_rate);
}
