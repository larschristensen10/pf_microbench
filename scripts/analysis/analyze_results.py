#!/usr/bin/env python3
"""
Analyze AMP L2 prefetcher microbenchmark results.

Usage:
    python3 scripts/analysis/analyze_results.py [RESULTS_DIR]

If RESULTS_DIR is omitted, uses the most recent run in results/microbench/.

Produces:
    - Per-benchmark plots (N vs hit_rate, with baseline overlay)
    - summary.txt with detected table sizes and characterization parameters
    - Anomaly warnings for broken/suspicious results

Degrades gracefully if matplotlib is not installed (text-only output).
"""
import csv
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_RESULTS_BASE = PROJECT_ROOT / "results" / "microbench"

# Benchmark definitions: (csv_stem, display_name, x_label, cliff_mode)
# cliff_mode: "falling" = detect where hit_rate drops (table-size tests)
#             "rising"  = detect where hit_rate rises (training characterization)
#             "none"    = no cliff detection (stride range, wait sensitivity)
BENCHMARK_DEFS = [
    ("training_length_train",  "Training Length",      "Training Accesses", "rising"),
    ("training_length_degree", "Prefetch Degree",      "Distance (lines)",  "falling"),
    ("training_length_wait",   "Wait Sensitivity",     "Wait (cycles)",     "rising"),
    ("stride_range",           "Stride Range",         "Stride (cachelines)", "none"),
    ("stream_tracker",         "Stream Tracker Table", "Streams",           "falling"),
    ("spatial_region_replay",  "Spatial Region (Replay)", "Pages",          "falling"),
    ("spatial_region_eviction","Spatial Region (Eviction)","Pages",         "falling"),
    ("history_buffer",         "History Buffer Depth", "Poison Accesses",   "falling"),
    ("ip_table",               "IP-based Pattern Table","IP Stubs",         "falling"),
]


def parse_csv(path):
    """Parse a benchmark CSV file. Returns list of dicts with numeric values."""
    rows = []
    if not path.exists():
        return rows
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    if not lines:
        return rows
    for line in lines:
        parts = line.split(",")
        if len(parts) < 9:
            continue
        try:
            rows.append({
                "n":        int(parts[0]),
                "median":   int(parts[1]),
                "p5":       int(parts[2]),
                "p95":      int(parts[3]),
                "min":      int(parts[4]),
                "max":      int(parts[5]),
                "hits":     int(parts[6]),
                "misses":   int(parts[7]),
                "hit_rate": float(parts[8]),
            })
        except (ValueError, IndexError):
            continue
    return rows


def detect_cliff_falling(rows, threshold=0.9, min_run=3):
    """Find the largest N where hit_rate >= threshold in a sustained run.

    Returns the cliff N, or None if no sustained high-hit region found.
    A "sustained run" means at least min_run consecutive points above threshold.
    """
    if not rows:
        return None
    # Find all runs of consecutive high-hit points
    best_cliff = None
    run_start = None
    run_len = 0
    for r in rows:
        if r["hit_rate"] >= threshold:
            if run_start is None:
                run_start = r["n"]
            run_len += 1
        else:
            if run_len >= min_run:
                # The cliff is at the last high-hit N before this drop
                best_cliff = rows[rows.index(r) - 1]["n"] if rows.index(r) > 0 else run_start
            run_start = None
            run_len = 0
    # Check if the run extends to the end
    if run_len >= min_run:
        best_cliff = rows[-1]["n"]
    return best_cliff


def detect_cliff_rising(rows, threshold=0.5):
    """Find the first N where hit_rate crosses above threshold.

    Used for training length characterization.
    """
    if not rows:
        return None
    for r in rows:
        if r["hit_rate"] >= threshold:
            return r["n"]
    return None


def check_anomalies(name, active, baseline):
    """Check for suspicious patterns in results."""
    warnings = []
    if not active:
        warnings.append(f"  WARNING: {name} — no active data (empty/missing CSV)")
        return warnings

    active_rates = [r["hit_rate"] for r in active]
    avg_active = sum(active_rates) / len(active_rates)

    if avg_active < 0.01 and len(active) > 5:
        warnings.append(f"  WARNING: {name} — active hit_rate near zero everywhere "
                        f"(avg={avg_active:.4f}). Prefetcher may not be working.")

    if baseline:
        baseline_rates = [r["hit_rate"] for r in baseline]
        avg_baseline = sum(baseline_rates) / len(baseline_rates)
        if avg_baseline > 0.1:
            warnings.append(f"  WARNING: {name} — baseline hit_rate unexpectedly high "
                            f"(avg={avg_baseline:.4f}). Possible measurement artifact.")

        # Check for inverted pattern (baseline higher than active)
        if len(active) == len(baseline) and len(active) > 10:
            inversions = sum(1 for a, b in zip(active_rates, baseline_rates)
                            if b > a + 0.05)
            if inversions > len(active) * 0.5:
                warnings.append(f"  WARNING: {name} — baseline hit_rate exceeds active "
                                f"in {inversions}/{len(active)} points. Results may be inverted.")
    return warnings


def generate_plots_to(out_dir, all_data):
    """Generate matplotlib plots if available. Returns True on success."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    for csv_stem, display_name, x_label, _ in BENCHMARK_DEFS:
        active = all_data.get(csv_stem, [])
        baseline = all_data.get(f"{csv_stem}_baseline", [])
        if not active and not baseline:
            continue

        fig, ax1 = plt.subplots(figsize=(10, 5))

        # Hit rate plot
        if active:
            ns = [r["n"] for r in active]
            hrs = [r["hit_rate"] for r in active]
            ax1.plot(ns, hrs, "b.-", label="Active (L2 AMP)", markersize=3, linewidth=1)

        if baseline:
            ns_b = [r["n"] for r in baseline]
            hrs_b = [r["hit_rate"] for r in baseline]
            ax1.plot(ns_b, hrs_b, "r.--", label="Baseline (all off)",
                     markersize=3, linewidth=1, alpha=0.7)

        ax1.set_xlabel(x_label)
        ax1.set_ylabel("Hit Rate")
        ax1.set_ylim(-0.05, 1.05)
        ax1.set_title(display_name)
        ax1.legend(loc="upper right")
        ax1.grid(True, alpha=0.3)

        # Latency on secondary axis
        if active:
            ax2 = ax1.twinx()
            medians = [r["median"] for r in active]
            ax2.plot(ns, medians, "g-", alpha=0.3, linewidth=0.8, label="Median cycles")
            ax2.set_ylabel("Median Latency (cycles)", color="green", alpha=0.5)
            ax2.tick_params(axis="y", labelcolor="green")

        fig.tight_layout()
        fig.savefig(out_dir / f"{csv_stem}.png", dpi=150)
        plt.close(fig)

    return True


def find_results_dir(arg=None):
    """Resolve the results directory from argument or auto-detect latest."""
    if arg:
        p = Path(arg)
        if p.is_dir():
            return p
        print(f"ERROR: {arg} is not a directory")
        sys.exit(1)

    if not DEFAULT_RESULTS_BASE.is_dir():
        print(f"ERROR: no results directory found at {DEFAULT_RESULTS_BASE}")
        sys.exit(1)

    runs = sorted(DEFAULT_RESULTS_BASE.iterdir())
    runs = [r for r in runs if r.is_dir()]
    if not runs:
        print(f"ERROR: no runs found in {DEFAULT_RESULTS_BASE}")
        sys.exit(1)

    latest = runs[-1]
    print(f"Auto-selected latest run: {latest}")
    return latest


def main():
    results_dir = find_results_dir(sys.argv[1] if len(sys.argv) > 1 else None)

    # Parse all CSVs
    all_data = {}
    for csv_stem, _, _, _ in BENCHMARK_DEFS:
        for suffix in ["", "_baseline"]:
            key = f"{csv_stem}{suffix}"
            path = results_dir / f"{key}.csv"
            all_data[key] = parse_csv(path)

    # Also try legacy name (spatial_region_transfer)
    for suffix in ["", "_baseline"]:
        legacy_key = f"spatial_region_transfer{suffix}"
        new_key = f"spatial_region_eviction{suffix}"
        if not all_data.get(new_key):
            path = results_dir / f"{legacy_key}.csv"
            if path.exists():
                all_data[new_key] = parse_csv(path)

    # Generate summary
    summary_lines = []
    summary_lines.append("=" * 60)
    summary_lines.append("AMP L2 Prefetcher Microbenchmark Analysis")
    summary_lines.append(f"Results: {results_dir}")
    summary_lines.append("=" * 60)

    # Characterization results
    summary_lines.append("\n--- Prefetcher Characterization ---\n")

    train_data = all_data.get("training_length_train", [])
    if train_data:
        min_train = detect_cliff_rising(train_data, threshold=0.5)
        if min_train is not None:
            summary_lines.append(f"  Minimum training length: {min_train} accesses")
        else:
            summary_lines.append("  Minimum training length: NOT DETECTED (hit_rate never reached 0.5)")
    else:
        summary_lines.append("  Training length: no data")

    degree_data = all_data.get("training_length_degree", [])
    if degree_data:
        cliff = detect_cliff_falling(degree_data, threshold=0.5, min_run=2)
        if cliff is not None:
            summary_lines.append(f"  Prefetch degree: ~{cliff} lines ahead")
        else:
            # Check if it never reaches 0.5
            max_hr = max(r["hit_rate"] for r in degree_data)
            if max_hr < 0.5:
                summary_lines.append(f"  Prefetch degree: NOT DETECTED (max hit_rate={max_hr:.4f})")
            else:
                summary_lines.append("  Prefetch degree: >64 lines (no cliff in range)")
    else:
        summary_lines.append("  Prefetch degree: no data")

    wait_data = all_data.get("training_length_wait", [])
    if wait_data:
        min_wait = detect_cliff_rising(wait_data, threshold=0.5)
        if min_wait is not None:
            summary_lines.append(f"  Minimum wait time: {min_wait} cycles")
        else:
            summary_lines.append("  Minimum wait time: NOT DETECTED")
    else:
        summary_lines.append("  Wait sensitivity: no data")

    stride_data = all_data.get("stride_range", [])
    if stride_data:
        detected = [r["n"] for r in stride_data if r["hit_rate"] >= 0.5]
        if detected:
            summary_lines.append(f"  Detected strides: {detected} (cachelines)")
            summary_lines.append(f"  Max detectable stride: {max(detected)} cachelines "
                                 f"({max(detected) * 64} bytes)")
        else:
            summary_lines.append("  Stride detection: NONE detected (all hit_rates < 0.5)")
    else:
        summary_lines.append("  Stride range: no data")

    # Table size results
    summary_lines.append("\n--- Table Size Estimates ---\n")

    table_benchmarks = [
        ("stream_tracker",         "Stream Tracker Table"),
        ("spatial_region_replay",  "Spatial Region Table (replay)"),
        ("spatial_region_eviction","Spatial Region Table (eviction)"),
        ("history_buffer",         "History Buffer Depth"),
        ("ip_table",               "IP-based Pattern Table"),
    ]

    for csv_stem, display_name in table_benchmarks:
        active = all_data.get(csv_stem, [])
        if not active:
            summary_lines.append(f"  {display_name}: no data")
            continue
        cliff = detect_cliff_falling(active)
        if cliff is not None:
            summary_lines.append(f"  {display_name}: {cliff} entries")
        else:
            max_hr = max(r["hit_rate"] for r in active) if active else 0
            if max_hr < 0.1:
                summary_lines.append(f"  {display_name}: INDETERMINATE "
                                     f"(hit_rate never sustained above 0.9, max={max_hr:.4f})")
            else:
                summary_lines.append(f"  {display_name}: >{active[-1]['n']} entries "
                                     f"(no cliff detected, max_hit_rate={max_hr:.4f})")

    # Anomaly detection
    summary_lines.append("\n--- Anomaly Check ---\n")
    any_anomaly = False
    for csv_stem, display_name, _, _ in BENCHMARK_DEFS:
        active = all_data.get(csv_stem, [])
        baseline = all_data.get(f"{csv_stem}_baseline", [])
        warnings = check_anomalies(display_name, active, baseline)
        if warnings:
            any_anomaly = True
            summary_lines.extend(warnings)
    if not any_anomaly:
        summary_lines.append("  No anomalies detected.")

    # Data inventory
    summary_lines.append("\n--- Data Inventory ---\n")
    for csv_stem, display_name, _, _ in BENCHMARK_DEFS:
        active = all_data.get(csv_stem, [])
        baseline = all_data.get(f"{csv_stem}_baseline", [])
        active_str = f"{len(active)} points" if active else "MISSING"
        baseline_str = f"{len(baseline)} points" if baseline else "MISSING"
        summary_lines.append(f"  {display_name}: active={active_str}, baseline={baseline_str}")

    summary_text = "\n".join(summary_lines) + "\n"

    # Print summary
    print(summary_text)

    # Save summary and plots
    out_dir = results_dir / "analysis"
    try:
        out_dir.mkdir(exist_ok=True)
    except PermissionError:
        # Results dir may be owned by root; fall back to current directory
        out_dir = Path.cwd() / "analysis_output"
        out_dir.mkdir(exist_ok=True)
        print(f"NOTE: Cannot write to {results_dir}/analysis/ (permission denied)")
        print(f"      Using fallback: {out_dir}/")

    summary_path = out_dir / "summary.txt"
    with open(summary_path, "w") as f:
        f.write(summary_text)
    print(f"Summary saved to: {summary_path}")

    # Generate plots
    if generate_plots_to(out_dir, all_data):
        print(f"Plots saved to: {out_dir}/")
    else:
        print("NOTE: matplotlib not available, skipping plots. Install with: pip install matplotlib")


if __name__ == "__main__":
    main()
