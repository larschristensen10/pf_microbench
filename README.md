# Intel AMP L2 Prefetcher Table Size Microbenchmarks

Reverse-engineer the sizes of internal tables in the Intel AMP (Adaptive Multipath Prefetcher) L2 prefetcher on Raptor Lake.

## System Requirements

- Raptor Lake (13th/14th Gen Intel Core) with P-cores
- Linux with `msr-tools` (`rdmsr`/`wrmsr`), `perf`, `cpupower`
- Root access (for MSR writes and hugepage reservation)
- GCC with `-march=native` support

## Directory Structure

```
pf_microbench/
├── benchmarks/
│   ├── common/
│   │   ├── timing.h           # rdtsc/rdtscp serialized cycle measurement
│   │   ├── memory.h           # hugepage alloc, clflush, timed_load, force_read
│   │   ├── calibrate.h        # latency calibration + stats interface
│   │   └── common.c           # calibration, statistics, CSV output, core pinning
│   ├── stream_tracker/
│   │   └── stream_tracker.c   # Stream tracker table size
│   ├── spatial_region/
│   │   └── spatial_region.c   # Spatial region (access map) table size
│   ├── history_buffer/
│   │   └── history_buffer.c   # History buffer depth
│   ├── ip_table/
│   │   └── ip_table.c         # IP-based pattern table size
│   ├── diagnostic.c           # Quick sanity check: is prefetcher active?
│   └── Makefile
├── configs/
│   ├── experiment_params.conf  # System params (target core, CPU frequency)
│   └── prefetcher_configs.txt  # MSR 0x1A4 configurations
├── scripts/
│   ├── setup/                  # System prep (existing)
│   ├── execution/
│   │   └── run_microbench_sweep.py  # Orchestrate all benchmarks
│   └── utils/                  # Logging/cleanup (existing)
└── results/
    └── microbench/             # CSV output directory
```

## What Each Benchmark Measures

### 1. Stream Tracker Table (`stream_tracker`)

**Table:** Tracks active stride-based prefetch streams. Each entry monitors one stream's stride and direction.

**Method:** Create N independent stride-1 streams in separate 512KB-spaced memory regions. Train stream 0 first (short: 4 lines), then train N-1 "evictor" streams (8 lines each). Resume stream 0 and measure whether the prefetcher runs ahead. If stream 0 was evicted from the tracker by the other streams, the prefetcher must re-learn the stride, and a line well beyond the resume point will be a cache miss.

**Sweep:** N = 1..128

### 2. Spatial Region Table (`spatial_region`)

**Table:** Per-page access maps recording which cache line offsets within a 4KB region were accessed. Enables spatial pattern replication on new pages.

**Method:** On N pages, access offsets {0, 2, 5} to train a spatial pattern. Then trigger offset 0 on a fresh (never-accessed) page and measure whether offset 2 was proactively prefetched. If the spatial table is full and the pattern has been evicted, no prefetch occurs.

**Modes:**
- `--mode replay` (default): Test cross-page transfer to a fresh page
- `--mode eviction`: Test if older pages' patterns are still retained

**Sweep:** N = 1..256

### 3. History Buffer (`history_buffer`)

**Table:** Records recent demand accesses for pattern detection. Determines how far back the prefetcher can look for stride/correlation patterns.

**Method:** Train a stride-1 pattern (4 lines), then inject N "poison" accesses on distinct pages to fill the history buffer. Resume the original pattern and measure whether the prefetcher picks it up immediately (pattern still in history) or must re-learn (pattern evicted).

**Sweep:** N = 0..512 (non-uniform steps for efficiency)

### 4. IP-based Pattern Table (`ip_table`)

**Table:** Maps instruction pointer (load PC) to associated stride/pattern, allowing instant recall without re-training.

**Method:** Identical to stream_tracker, but each stream's loads go through a distinct code stub (`mmap`'d `mov (%rdi),%rax; ret` at unique virtual addresses). Compare the cliff point with stream_tracker results to determine if the IP table is a separate bottleneck.

**Sweep:** N = 1..128

## MSR 0x1A4 Prefetcher Control

| Bit | Prefetcher | Set bit = disabled |
|-----|-------------------------------|---|
| 0   | L2 HW prefetcher (AMP)        | Target of this study |
| 1   | L2 adjacent cache line        | |
| 2   | DCU (L1d) HW prefetcher       | |
| 3   | DCU (L1d) IP-based stride     | |

Key configurations in `configs/prefetcher_configs.txt`:

| MSR Value | Effect |
|-----------|--------|
| `0x00`    | All prefetchers enabled (default) |
| `0x0E`    | **Only L2 AMP enabled** (used for active tests) |
| `0x0F`    | All prefetchers disabled (used for baseline) |

## Quick Start

```bash
# Build
make -C benchmarks/

# Run the full sweep (as root)
sudo python3 scripts/execution/run_microbench_sweep.py

# Or run individual benchmarks manually:
# First enable only the L2 AMP prefetcher:
sudo wrmsr -a 0x1A4 0x0E
# Reserve hugepages:
echo 64 | sudo tee /proc/sys/vm/nr_hugepages
# Run:
benchmarks/stream_tracker/stream_tracker --reps 5000 > results.csv
```

## Diagnostic

To verify the prefetcher is active before running the full sweep:

```bash
gcc -O2 -march=native -I benchmarks/common -o benchmarks/diagnostic \
    benchmarks/diagnostic.c benchmarks/common/common.c
sudo benchmarks/diagnostic
```

This runs a simple stride-1 test and reports whether lines beyond training are L2 hits (prefetched) or DRAM misses (not prefetched).

## Interpreting Results

Each benchmark outputs CSV with columns:
```
N, median_cycles, p5, p95, min, max, hits, misses, hit_rate
```

Plot `N` (x-axis) vs `hit_rate` (y-axis). The expected shape:

```
hit_rate
  1.0 |.........
      |         .
      |          .
      |           .
  0.0 |            ..........
      +----+-------+---------> N
           K (table size)
```

**Table size K** = largest N where `hit_rate >= 0.9`.

## Core Technique

All benchmarks follow the pattern: **flush → train → (evict) → resume → wait → measure**

- **Timing:** `lfence; rdtsc` before load, `rdtscp; lfence` after (serialized, ~50 cycle overhead)
- **Cache control:** `clflush` to evict specific lines before each trial
- **Memory:** 2MB hugepages to eliminate TLB noise
- **Calibration:** Each benchmark auto-calibrates L2 hit vs DRAM latency at startup to set the hit/miss threshold
- **Repetitions:** 5000 per sweep point (configurable), reporting median and percentiles

## Common Library (`benchmarks/common/`)

| File | Contents |
|------|----------|
| `timing.h` | `rdtsc_start()`, `rdtsc_fenced()`, `rdtsc_end()` — serialized TSC reads |
| `memory.h` | `alloc_hugepages()`, `clflush()`, `mfence()`, `force_read()`, `timed_load()`, `delay_cycles()` |
| `calibrate.h` | `latency_profile_t`, `measurement_stats_t` — type definitions |
| `common.c` | `calibrate_latencies()`, `pin_to_core()`, `compute_stats()`, CSV output helpers |

## Orchestration Script

`scripts/execution/run_microbench_sweep.py` automates the full measurement campaign:

1. Reserve 2MB hugepages
2. Build benchmarks
3. Set MSR to `0x0E` (only L2 AMP enabled)
4. Run all 5 benchmark variants (stream_tracker, spatial_region replay, spatial_region eviction, history_buffer, ip_table)
5. Run baseline with MSR `0x0F` (all disabled) for comparison
6. Restore MSR to `0x00` and release hugepages

Results are saved to `results/microbench/{timestamp}/`.
