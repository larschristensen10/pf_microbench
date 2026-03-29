/*
 * Quick diagnostic: is the L2 prefetcher actually enabled?
 * Runs the simplest possible prefetch detection test.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "common/memory.h"
#include "common/calibrate.h"

#define REPS 500

int main(void)
{
    pin_to_core(0);

    size_t buf_size = 4 * 1024 * 1024;
    volatile char *buf = (volatile char *)mmap(NULL, buf_size,
        PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    memset((void *)buf, 0, buf_size);

    /* --- Calibration: known L1d hit and known DRAM miss --- */
    uint64_t cal_l1[REPS], cal_dram[REPS];

    for (int i = 0; i < REPS; i++) {
        force_read(buf);  /* warm into L1d */
        cal_l1[i] = timed_load(buf);  /* should be L1d hit */
    }

    for (int i = 0; i < REPS; i++) {
        clflush(buf);
        mfence();
        cal_dram[i] = timed_load(buf);  /* should be DRAM */
    }

    /* Sort and report medians */
    measurement_stats_t s_l1 = compute_stats(cal_l1, REPS, 999999);
    measurement_stats_t s_dram = compute_stats(cal_dram, REPS, 999999);

    printf("L1d hit:    median=%lu  p5=%lu  p95=%lu\n",
           (unsigned long)s_l1.median, (unsigned long)s_l1.p5, (unsigned long)s_l1.p95);
    printf("DRAM miss:  median=%lu  p5=%lu  p95=%lu\n",
           (unsigned long)s_dram.median, (unsigned long)s_dram.p5, (unsigned long)s_dram.p95);

    uint64_t threshold = (s_l1.median + s_dram.median) / 2;
    printf("Threshold:  %lu\n\n", (unsigned long)threshold);

    /* --- Test 1: Sequential stride-1, measure beyond training --- */
    printf("=== Test 1: stride-1 sequential, measure lines beyond training ===\n");
    printf("(Flush 0-30, train 0-15, wait, measure 16-23)\n");

    for (int measure_at = 16; measure_at <= 23; measure_at++) {
        uint64_t samples[REPS];
        for (int rep = 0; rep < REPS; rep++) {
            /* Flush */
            for (int l = 0; l < 30; l++)
                clflush(buf + (size_t)l * 64);
            mfence();

            /* Train stride-1 */
            for (int l = 0; l < 16; l++) {
                compiler_barrier();
                force_read(buf + (size_t)l * 64);
                delay_cycles(TRAIN_DELAY);
            }
            compiler_barrier();

            /* Wait for prefetcher */
            delay_cycles(2000);

            /* Measure */
            samples[rep] = timed_load(buf + (size_t)measure_at * 64);
        }
        measurement_stats_t s = compute_stats(samples, REPS, threshold);
        printf("  line %2d: median=%3lu  hit_rate=%.2f\n",
               measure_at, (unsigned long)s.median,
               (double)s.hit_count / REPS);
    }

    /* --- Test 2: Without any training (negative control) --- */
    printf("\n=== Test 2: No training, just flush and measure (should be all DRAM) ===\n");
    for (int measure_at = 0; measure_at <= 3; measure_at++) {
        uint64_t samples[REPS];
        for (int rep = 0; rep < REPS; rep++) {
            for (int l = 0; l < 20; l++)
                clflush(buf + (size_t)l * 64);
            mfence();
            delay_cycles(2000);
            samples[rep] = timed_load(buf + (size_t)measure_at * 64);
        }
        measurement_stats_t s = compute_stats(samples, REPS, threshold);
        printf("  line %2d: median=%3lu  hit_rate=%.2f\n",
               measure_at, (unsigned long)s.median,
               (double)s.hit_count / REPS);
    }

    /* --- Test 3: Train + measure on same line (should be cache hit) --- */
    printf("\n=== Test 3: Access then re-measure same line (should be L1d hit) ===\n");
    {
        uint64_t samples[REPS];
        for (int rep = 0; rep < REPS; rep++) {
            clflush(buf);
            mfence();
            force_read(buf);  /* bring into cache */
            samples[rep] = timed_load(buf);  /* should hit L1d */
        }
        measurement_stats_t s = compute_stats(samples, REPS, threshold);
        printf("  line  0: median=%3lu  hit_rate=%.2f\n",
               (unsigned long)s.median,
               (double)s.hit_count / REPS);
    }

    munmap((void *)buf, buf_size);
    return 0;
}
