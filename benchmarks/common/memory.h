#pragma once
#include <sys/mman.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <stdint.h>
#include "timing.h"

#define CACHELINE_SIZE 64
#define PAGE_SIZE      4096
#define HUGEPAGE_SIZE  (2 * 1024 * 1024)  /* 2MB */

/* Allocate buffer using 2MB hugepages. Falls back to regular pages with a
 * warning if hugepages are unavailable. All pages are faulted in. */
static inline void *alloc_hugepages(size_t size)
{
    size_t aligned = (size + HUGEPAGE_SIZE - 1) & ~((size_t)HUGEPAGE_SIZE - 1);
    void *ptr = mmap(NULL, aligned,
                     PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB,
                     -1, 0);
    if (ptr == MAP_FAILED) {
        fprintf(stderr, "WARNING: hugepage mmap failed (%s), "
                "falling back to regular pages. "
                "TLB misses may affect results.\n",
                strerror(errno));
        ptr = mmap(NULL, aligned,
                   PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS,
                   -1, 0);
    }
    if (ptr != MAP_FAILED) {
        memset(ptr, 0, aligned);  /* fault all pages */
    }
    return ptr;
}

static inline void free_hugepages(void *ptr, size_t size)
{
    size_t aligned = (size + HUGEPAGE_SIZE - 1) & ~((size_t)HUGEPAGE_SIZE - 1);
    munmap(ptr, aligned);
}

/* Flush a single cache line from all cache levels. */
static inline void clflush(volatile void *addr)
{
    asm volatile("clflush (%0)" : : "r"(addr) : "memory");
}

/* Full memory fence (store + load ordering). */
static inline void mfence(void)
{
    asm volatile("mfence" ::: "memory");
}

/* Compiler-only barrier -- prevents reordering but no hw fence. */
static inline void compiler_barrier(void)
{
    asm volatile("" ::: "memory");
}

/* Force a 64-bit load that the compiler cannot optimize away.
 * Returns the loaded value (useful to create a data dependency). */
static inline uint64_t force_read(volatile void *addr)
{
    uint64_t val;
    asm volatile("mov (%1), %0" : "=r"(val) : "r"(addr) : "memory");
    return val;
}

/* Spin delay: busy-wait for approximately `cycles` TSC ticks.
 * Used to give the prefetcher time to issue and complete fetches. */
static inline void delay_cycles(uint64_t cycles)
{
    uint64_t start = rdtsc_fenced();
    while (rdtsc_fenced() - start < cycles) {}
}

/* Timed load: measure the latency of a single load from addr in cycles.
 * Uses explicit mov in inline asm to prevent compiler from eliminating
 * or reordering the load. */
static inline uint64_t timed_load(volatile void *addr)
{
    uint32_t lo0, hi0, lo1, hi1;

    asm volatile(
        "lfence\n\t"
        "rdtsc\n\t"
        "mov %%eax, %0\n\t"
        "mov %%edx, %1\n\t"
        "mov (%4), %%rax\n\t"
        "rdtscp\n\t"
        "lfence\n\t"
        "mov %%eax, %2\n\t"
        "mov %%edx, %3\n\t"
        : "=r"(lo0), "=r"(hi0), "=r"(lo1), "=r"(hi1)
        : "r"(addr)
        : "rax", "rcx", "rdx", "memory"
    );

    uint64_t t0 = ((uint64_t)hi0 << 32) | lo0;
    uint64_t t1 = ((uint64_t)hi1 << 32) | lo1;
    return t1 - t0;
}
