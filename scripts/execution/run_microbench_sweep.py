#!/usr/bin/env python3
"""
Orchestration script for AMP L2 prefetcher table-size microbenchmarks.

Usage (run as root):
    python3 scripts/execution/run_microbench_sweep.py [--core 0] [--run-name NAME]

Steps:
  1. Reserve hugepages
  2. Prepare system (disable turbo, lock freq, isolate core)
  3. Build microbenchmarks
  4. Run each benchmark with L2 AMP only (MSR 0x0E)
  5. Run baseline with all prefetchers disabled (MSR 0x0F)
  6. Clean up: release hugepages, restore system
"""
import argparse
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
BENCH_DIR = os.path.join(PROJECT_ROOT, "benchmarks")
RESULTS_DIR = os.path.join(PROJECT_ROOT, "results", "microbench")

BENCHMARKS = [
    ("stream_tracker", "stream_tracker/stream_tracker", []),
    ("spatial_region_replay", "spatial_region/spatial_region", ["--mode", "replay"]),
    ("spatial_region_transfer", "spatial_region/spatial_region", ["--mode", "transfer"]),
    ("history_buffer", "history_buffer/history_buffer", []),
    ("ip_table", "ip_table/ip_table", []),
]

# MSR 0x1A4 values
MSR_L2_AMP_ONLY = "0x0E"   # only L2 HW prefetcher enabled
MSR_ALL_DISABLED = "0x0F"   # all prefetchers disabled


def run_cmd(cmd, check=True, capture=False):
    """Run a shell command, optionally capturing output."""
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if check and result.returncode != 0:
        print(f"ERROR: command failed with rc={result.returncode}")
        if capture and result.stderr:
            print(result.stderr)
        sys.exit(1)
    return result


def set_msr(value):
    """Write MSR 0x1A4 on all cores."""
    # Find number of CPUs
    cpu_count = os.cpu_count() or 1
    for cpu in range(cpu_count):
        run_cmd(["wrmsr", "-p", str(cpu), "0x1A4", value])
    # Verify
    for cpu in range(cpu_count):
        result = run_cmd(["rdmsr", "-p", str(cpu), "0x1A4"], capture=True)
        actual = result.stdout.strip()
        expected = value.replace("0x", "").lstrip("0") or "0"
        if actual != expected and actual != value.replace("0x", ""):
            print(f"WARNING: CPU {cpu} MSR 0x1A4 = {actual}, expected {expected}")


def reserve_hugepages(n_pages=64):
    """Reserve 2MB hugepages."""
    hp_path = "/proc/sys/vm/nr_hugepages"
    with open(hp_path, "r") as f:
        current = int(f.read().strip())
    if current < n_pages:
        print(f"  Reserving {n_pages} hugepages (currently {current})")
        with open(hp_path, "w") as f:
            f.write(str(n_pages))
        time.sleep(1)
        with open(hp_path, "r") as f:
            actual = int(f.read().strip())
        if actual < n_pages:
            print(f"WARNING: only got {actual}/{n_pages} hugepages")
    else:
        print(f"  Already have {current} hugepages (>= {n_pages})")


def release_hugepages():
    """Release reserved hugepages."""
    hp_path = "/proc/sys/vm/nr_hugepages"
    with open(hp_path, "w") as f:
        f.write("0")


def main():
    parser = argparse.ArgumentParser(description="Run AMP prefetcher microbenchmarks")
    parser.add_argument("--core", type=int, default=0,
                        help="CPU core to run benchmarks on (default: 0)")
    parser.add_argument("--run-name", type=str, default="",
                        help="Optional name suffix for results directory")
    parser.add_argument("--skip-baseline", action="store_true",
                        help="Skip the all-disabled baseline run")
    parser.add_argument("--reps", type=int, default=5000,
                        help="Repetitions per sweep point (default: 5000)")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("ERROR: this script must be run as root (for MSR access and hugepages)")
        sys.exit(1)

    # Create results directory
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_name = f"{timestamp}"
    if args.run_name:
        run_name += f"_{args.run_name}"
    run_dir = os.path.join(RESULTS_DIR, run_name)
    os.makedirs(run_dir, exist_ok=True)
    print(f"Results directory: {run_dir}")

    # Step 1: Reserve hugepages
    print("\n=== Step 1: Reserve hugepages ===")
    reserve_hugepages(64)

    # Step 2: Build benchmarks
    print("\n=== Step 2: Build benchmarks ===")
    run_cmd(["make", "-C", BENCH_DIR, "clean"])
    run_cmd(["make", "-C", BENCH_DIR, "-j2"])

    # Step 3: Run with L2 AMP only
    print("\n=== Step 3: Run benchmarks (L2 AMP only, MSR=0x0E) ===")
    set_msr(MSR_L2_AMP_ONLY)

    for name, binary_path, extra_args in BENCHMARKS:
        binary = os.path.join(BENCH_DIR, binary_path)
        out_file = os.path.join(run_dir, f"{name}.csv")
        print(f"\n--- Running {name} ---")

        cmd = [binary, "--core", str(args.core), "--reps", str(args.reps)] + extra_args
        with open(out_file, "w") as f:
            result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True)
            if result.stderr:
                print(result.stderr, end="")
            if result.returncode != 0:
                print(f"WARNING: {name} exited with rc={result.returncode}")
        print(f"  -> {out_file}")

    # Step 4: Baseline (all disabled)
    if not args.skip_baseline:
        print("\n=== Step 4: Run baseline (all prefetchers disabled, MSR=0x0F) ===")
        set_msr(MSR_ALL_DISABLED)

        for name, binary_path, extra_args in BENCHMARKS:
            binary = os.path.join(BENCH_DIR, binary_path)
            out_file = os.path.join(run_dir, f"{name}_baseline.csv")
            print(f"\n--- Running {name} (baseline) ---")

            cmd = [binary, "--core", str(args.core), "--reps", str(args.reps)] + extra_args
            with open(out_file, "w") as f:
                result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True)
                if result.stderr:
                    print(result.stderr, end="")
                if result.returncode != 0:
                    print(f"WARNING: {name} baseline exited with rc={result.returncode}")
            print(f"  -> {out_file}")

    # Step 5: Cleanup
    print("\n=== Step 5: Cleanup ===")
    set_msr("0x00")  # restore all prefetchers enabled
    release_hugepages()

    # Fix permissions so non-root can read results
    run_cmd(["chmod", "-R", "a+rX", run_dir], check=False)

    print(f"\n=== Done! Results in: {run_dir} ===")
    print("Analyze with: look for the N value where hit_rate drops below 0.9")


if __name__ == "__main__":
    main()
