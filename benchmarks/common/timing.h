#pragma once
#include <stdint.h>

/*
 * Serialized TSC reading primitives for cycle-accurate latency measurement.
 *
 * Usage pattern for timing a single load:
 *   uint64_t t0 = rdtsc_fenced();   // lfence + rdtsc
 *   ... load ...
 *   uint64_t t1 = rdtsc_end();      // rdtscp + lfence
 *   uint64_t cycles = t1 - t0;
 */

/* Full serialization: CPUID flushes pipeline, then RDTSC.
 * Use at outer measurement boundaries (e.g., start of calibration loop). */
static inline uint64_t rdtsc_start(void)
{
    uint32_t lo, hi;
    asm volatile(
        "cpuid\n\t"
        "rdtsc\n\t"
        : "=a"(lo), "=d"(hi)
        : "a"(0)
        : "rbx", "rcx"
    );
    return ((uint64_t)hi << 32) | lo;
}

/* Lightweight serialized read: LFENCE ensures all prior loads have retired,
 * then RDTSC. ~20 cycles less overhead than CPUID-based variant. */
static inline uint64_t rdtsc_fenced(void)
{
    uint32_t lo, hi;
    asm volatile(
        "lfence\n\t"
        "rdtsc\n\t"
        : "=a"(lo), "=d"(hi)
        :
        :
    );
    return ((uint64_t)hi << 32) | lo;
}

/* End measurement: RDTSCP waits for prior instructions to complete,
 * then LFENCE prevents later instructions from affecting the reading. */
static inline uint64_t rdtsc_end(void)
{
    uint32_t lo, hi;
    asm volatile(
        "rdtscp\n\t"
        "lfence\n\t"
        : "=a"(lo), "=d"(hi)
        :
        : "rcx"
    );
    return ((uint64_t)hi << 32) | lo;
}
