#pragma once
#include <stdint.h>
#include <stdio.h>

typedef struct {
    uint64_t l2_hit_median;
    uint64_t dram_median;
    uint64_t hit_miss_threshold;
} latency_profile_t;

typedef struct {
    uint64_t median;
    uint64_t p5;
    uint64_t p95;
    uint64_t min;
    uint64_t max;
    int hit_count;
    int miss_count;
} measurement_stats_t;

/* Calibrate L2 hit and DRAM latencies using the provided buffer.
 * Buffer must be pre-allocated and faulted in. */
latency_profile_t calibrate_latencies(void *buffer);

/* Pin current process to a single core. */
void pin_to_core(int core_id);

/* Compute statistics from an array of cycle measurements. */
measurement_stats_t compute_stats(uint64_t *samples, int n, uint64_t threshold);

/* Print CSV header with metadata comments. */
void print_csv_header(FILE *out, const char *test_name);

/* Print one CSV data row. */
void print_csv_row(FILE *out, int param_value, measurement_stats_t *stats, int reps);
