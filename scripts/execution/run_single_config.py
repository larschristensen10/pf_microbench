#!/usr/bin/env python3
"""
Run All Benchmarks for a Single Configuration
Executes all benchmarks in the workload list for one prefetcher config
Run with: sudo python3 scripts/execution/run_single_config.py WORKLOAD_TYPE CONFIG_ID
"""

import os
import sys
import subprocess
import configparser
from datetime import datetime


class TeeLogger:
    """Mirror stdout/stderr to both console and log file"""
    def __init__(self, stream, log_file):
        self.stream = stream
        self.log_file = log_file

    def write(self, data):
        self.stream.write(data)
        self.stream.flush()  # Flush immediately to console
        self.log_file.write(data)
        self.log_file.flush()  # Flush immediately to file

    def flush(self):
        self.stream.flush()
        self.log_file.flush()


def setup_logging(study_root, workload_type, config_id, run_dir):
    """Initialize per-config execution log"""
    # Use per-run directory if provided
    if run_dir:
        log_dir = os.path.join(run_dir, workload_type, 'logs')
    else:
        # Fallback for standalone runs
        log_dir = os.path.join(study_root, 'results', workload_type, 'logs')
    
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(log_dir, f'run_single_config_{workload_type}_cfg{config_id}_{timestamp}.log')

    # Open with line buffering (buffering=1) for immediate writes
    log_file = open(log_path, 'w', buffering=1)
    sys.stdout = TeeLogger(sys.stdout, log_file)
    sys.stderr = TeeLogger(sys.stderr, log_file)

    print(f"Logging to: {log_path}")
    return log_path

def load_config(study_root):
    """Load configuration"""
    config_file = os.path.join(study_root, 'configs', 'experiment_params.conf')
    
    if not os.path.exists(config_file):
        print(f"Error: Configuration file not found: {config_file}")
        sys.exit(1)
    
    config = configparser.ConfigParser()
    config.read(config_file)
    
    return {
        'REPETITIONS': int(config.get('experiment', 'REPETITIONS', fallback='10')),
    }

def load_full_config(study_root):
    """Load full configuration including target core"""
    config_file = os.path.join(study_root, 'configs', 'experiment_params.conf')
    
    if not os.path.exists(config_file):
        return {}
    
    config = configparser.ConfigParser()
    config.read(config_file)
    
    return {
        'TARGET_CORE': config.get('system', 'TARGET_CORE', fallback='0'),
        'REPETITIONS': int(config.get('experiment', 'REPETITIONS', fallback='10')),
    }

def load_benchmarks(study_root, workload_type):
    """Load benchmark list for workload type"""
    if workload_type == 'intspeed':
        bench_file = os.path.join(study_root, 'configs', 'intspeed_benchmarks.txt')
    else:
        bench_file = os.path.join(study_root, 'configs', 'fpspeed_benchmarks.txt')
    
    benchmarks = []
    try:
        with open(bench_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    benchmarks.append(line)
    except Exception as e:
        print(f"Error reading benchmark file: {e}")
        sys.exit(1)
    
    return benchmarks

def set_prefetchers(config_id, study_root):
    """Set prefetcher configuration"""
    setup_dir = os.path.join(study_root, 'scripts', 'setup')
    script = os.path.join(setup_dir, 'set_prefetchers.py')
    
    print(f"Setting prefetcher configuration {config_id}...")
    # Run without capturing output so it streams in real-time
    result = subprocess.run(['sudo', 'python3', script, str(config_id)],
                          stdout=None, stderr=None)
    
    if result.returncode != 0:
        print("Error: Failed to set prefetcher configuration")
        sys.exit(1)
    print()

def run_single_benchmark(config_id, rep_num, benchmark, workload_type, study_root, run_dir):
    """Run a single benchmark"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(script_dir, 'run_benchmark_with_metrics.py')
    
    # Build command with explicit run_dir argument
    cmd = ['sudo', 'python3', script, '--run-dir', run_dir, str(config_id), str(rep_num), 
           benchmark, workload_type]
    
    # Run without capturing output so it streams in real-time
    result = subprocess.run(cmd, stdout=None, stderr=None)
    sys.stdout.flush()  # Ensure output is flushed
    return result.returncode == 0

def cleanup_state(study_root):
    """Clean up system state between runs"""
    utils_dir = os.path.join(study_root, 'scripts', 'utils')
    script = os.path.join(utils_dir, 'cleanup_state.py')
    
    subprocess.run(['sudo', 'python3', script], capture_output=True)

def main():
    """Main function"""
    # Parse arguments with support for --run-dir
    run_dir = None
    args = sys.argv[1:]
    
    if '--run-dir' in args:
        idx = args.index('--run-dir')
        if idx + 1 < len(args):
            run_dir = args[idx + 1]
            args = args[:idx] + args[idx+2:]  # Remove --run-dir and its value
    
    if len(args) != 2:
        print("Usage: python3 run_single_config.py [--run-dir /path/to/run] WORKLOAD_TYPE CONFIG_ID")
        print("  --run-dir: Optional path to per-run directory")
        print("  WORKLOAD_TYPE: intspeed or fpspeed")
        print("  CONFIG_ID: 0-7")
        sys.exit(1)
    
    workload_type = args[0]
    config_id = args[1]
    
    if workload_type not in ['intspeed', 'fpspeed']:
        print("Error: WORKLOAD_TYPE must be 'intspeed' or 'fpspeed'")
        sys.exit(1)
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    study_root = os.path.dirname(os.path.dirname(script_dir))

    log_path = setup_logging(study_root, workload_type, config_id, run_dir)
    exit_code = 0
    
    try:
        config = load_config(study_root)
        benchmarks = load_benchmarks(study_root, workload_type)
        repetitions = config['REPETITIONS']
        
        print("=========================================")
        print(f"Running Configuration {config_id}")
        print("=========================================")
        print(f"Workload: {workload_type}")
        print(f"Benchmarks: {len(benchmarks)}")
        print(f"Repetitions: {repetitions}")
        print(f"Total runs: {len(benchmarks) * repetitions}")
        print()
        
        # Pin orchestration script to non-benchmark cores to avoid interference
        # Note: TARGET_CORE isn't in this script's config, but we can infer from experiment_params.conf
        config_full = load_full_config(study_root)
        if 'TARGET_CORE' in config_full:
            target_core = int(config_full['TARGET_CORE'])
            total_cpus = os.cpu_count() or 1
            if total_cpus > 1:
                other_cores = {core for core in range(total_cpus) if core != target_core}
                if other_cores:
                    os.sched_setaffinity(0, other_cores)
                    print(f"Pinned orchestration to cores: {sorted(other_cores)}")
                    print(f"Benchmark core {target_core} isolated from Python runtime")
                    print()
        
        # Set prefetchers
        set_prefetchers(config_id, study_root)
        
        # Run benchmarks
        total_runs = len(benchmarks) * repetitions
        current_run = 0
        successful_runs = 0
        failed_runs = 0
        
        start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"Start time: {start_time}")
        print()
        
        for rep in range(1, repetitions + 1):
            print(f"--- Repetition {rep}/{repetitions} ---")
            
            for benchmark in benchmarks:
                current_run += 1
                print(f"[{current_run}/{total_runs}] Running {benchmark} (rep {rep})...")
                
                if run_single_benchmark(config_id, rep, benchmark, workload_type, study_root, run_dir):
                    successful_runs += 1
                    print("  ✓ Success")
                else:
                    failed_runs += 1
                    print("  ✗ Failed (logged)")
                
                # Cleanup between runs
                print("  Cleaning up state...")
                cleanup_state(study_root)
                print()
        
        # Summary
        end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print("")
        print("=========================================")
        print("Configuration Complete")
        print("=========================================")
        print(f"Start time: {start_time}")
        print(f"End time: {end_time}")
        print(f"Total runs: {total_runs}")
        print(f"Successful: {successful_runs}")
        print(f"Failed: {failed_runs}")
        
        exit_code = 0 if failed_runs == 0 else 1
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        exit_code = 130
    finally:
        print("")
        print(f"Final log location: {log_path}")

    sys.exit(exit_code)

if __name__ == '__main__':
    main()
