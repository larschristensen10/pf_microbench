#!/usr/bin/env python3
"""
Run Single SPEC Benchmark with Performance Metrics
Wraps SPEC execution with perf monitoring and core pinning
Run with: sudo python3 scripts/execution/run_benchmark_with_metrics.py CONFIG_ID REP_NUM BENCHMARK WORKLOAD_TYPE
"""

import os
import sys
import subprocess
import configparser
from datetime import datetime
import signal

def load_config():
    """Load configuration"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    study_root = os.path.dirname(os.path.dirname(script_dir))
    config_file = os.path.join(study_root, 'configs', 'experiment_params.conf')
    
    if not os.path.exists(config_file):
        print(f"Error: Configuration file not found: {config_file}")
        sys.exit(1)
    
    config = configparser.ConfigParser()
    config.read(config_file)
    
    return study_root, {
        'TARGET_CORE': config.get('system', 'TARGET_CORE', fallback='0'),
        'SPEC_ROOT': config.get('spec', 'SPEC_ROOT', fallback='/home/kc/benchmarks/SPEC17-Working'),
        'SPEC_CONFIG': config.get('spec', 'SPEC_CONFIG', fallback='Alderlake-gcc-linux-x86.cfg'),
    }

def load_perf_events(study_root):
    """Load perf events from configuration"""
    events_file = os.path.join(study_root, 'metrics', 'perf_events.txt')
    events = []
    try:
        with open(events_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    events.append(line)
    except Exception as e:
        print(f"Warning: Could not load perf events: {e}")
        events = ['cycles', 'instructions', 'cache-references', 'cache-misses']
    
    return ','.join(events)

def setup_output_dirs(study_root, workload_type, run_dir):
    """Create output directories with error handling"""
    # Use provided run_dir if available
    if run_dir:
        raw_dir = os.path.join(run_dir, workload_type, 'raw')
        log_dir = os.path.join(run_dir, workload_type, 'logs')
        failed_log = os.path.join(run_dir, 'failed_runs.log')
    else:
        # Fallback for standalone runs - MUST be in results directory
        raw_dir = os.path.join(study_root, 'results', workload_type, 'raw')
        log_dir = os.path.join(study_root, 'results', workload_type, 'logs')
        failed_log = os.path.join(study_root, 'results', 'failed_runs.log')
    
    try:
        os.makedirs(raw_dir, exist_ok=True)
        os.makedirs(log_dir, exist_ok=True)
    except Exception as e:
        print(f"FATAL ERROR: Could not create output directories")
        print(f"Raw dir: {raw_dir}")
        print(f"Log dir: {log_dir}")
        print(f"Error: {e}")
        print(f"Aborting to prevent writing data to wrong location")
        sys.exit(1)
    
    return raw_dir, log_dir, failed_log

def run_benchmark(config_id, rep_num, benchmark, workload_type, study_root, config, run_dir):
    """Execute the benchmark with perf monitoring"""
    
    raw_dir, log_dir, failed_log = setup_output_dirs(study_root, workload_type, run_dir)
    target_core = config['TARGET_CORE']
    spec_root = config['SPEC_ROOT']
    spec_config = config['SPEC_CONFIG']
    utils_dir = os.path.join(study_root, 'scripts', 'utils')
    
    # Output files
    base_name = f"{benchmark}_config{config_id}_rep{rep_num}"
    perf_out = os.path.join(raw_dir, f"{base_name}_perf.txt")
    spec_log = os.path.join(log_dir, f"{base_name}_spec.log")
    state_log = os.path.join(log_dir, f"{base_name}_state.log")
    
    print("=========================================")
    print(f"Running: {benchmark}")
    print(f"Config: {config_id}, Rep: {rep_num}")
    print(f"Workload: {workload_type}")
    print("=========================================")
    
    # Log system state before run
    subprocess.run(['python3', os.path.join(utils_dir, 'log_system_state.py'), state_log],
                  capture_output=True)
    
    # Load perf events
    perf_events = load_perf_events(study_root)
    
    # Change to SPEC directory
    os.chdir(spec_root)
    
    # Build SPEC command
    runcpu_exec = os.path.join(spec_root, 'bin', 'runcpu')
    if not os.path.exists(runcpu_exec):
        print(f"✗ runcpu not found at: {runcpu_exec}")
        return False

    # Get original user (script is run with sudo, but we want runcpu to run as normal user)
    original_user = os.environ.get('SUDO_USER', os.environ.get('USER', 'kc'))
    
    # Build runcpu command that will run as normal user
    # cam4_s requires unlimited stack size
    stack_limit = "ulimit -s unlimited && " if benchmark == "627.cam4_s" else ""
    runcpu_cmd = (f"cd {spec_root} && source ./shrc && {stack_limit}taskset -c {target_core} "
                 f"'{runcpu_exec}' --config={spec_config} --action=run --iterations=1 "
                 f"--noreportable {benchmark}")
    
    # Build full command: perf (as root) monitors runcpu (as normal user)
    # --cpu restricts perf to only monitor the target core
    perf_cmd = f"perf stat --cpu {target_core} -e {perf_events} -o {perf_out}"
    user_cmd = f"sudo -u {original_user} bash -c \"{runcpu_cmd}\""
    exec_cmd = f"{perf_cmd} -- {user_cmd}"
    
    print(f"Starting benchmark on core {target_core} as user {original_user}...")
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Execute
    with open(spec_log, 'w') as log_file:
        result = subprocess.run(['bash', '-c', exec_cmd], stdout=log_file, stderr=subprocess.STDOUT)
    
    exit_code = result.returncode
    end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"End time: {end_time}")
    
    # Handle result
    if exit_code == 0:
        print("✓ Benchmark completed successfully")
        return True
    elif exit_code == 124:
        print("✗ Benchmark timed out")
        with open(failed_log, 'a') as f:
            f.write(f"{end_time}|{workload_type}|{config_id}|{rep_num}|{benchmark}|TIMEOUT\n")
        return False
    else:
        print(f"✗ Benchmark failed with exit code {exit_code}")
        print(f"  Spec log: {spec_log}")
        # Extract error from log
        try:
            with open(spec_log) as f:
                lines = f.readlines()
                error_msg = next(
                    (l.strip() for l in lines[-20:] if any(k in l.lower() for k in ['error', 'failed', 'fatal', 'not found'])),
                    next((l.strip() for l in reversed(lines) if l.strip()), "Unknown error")
                )
        except:
            error_msg = "Unknown error"
        
        with open(failed_log, 'a') as f:
            f.write(f"{end_time}|{workload_type}|{config_id}|{rep_num}|{benchmark}|FAILED|{error_msg}\n")
        return False

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
    
    if len(args) != 4:
        print("Usage: python3 run_benchmark_with_metrics.py [--run-dir /path/to/run] CONFIG_ID REP_NUM BENCHMARK WORKLOAD_TYPE")
        print("  --run-dir: Optional path to per-run directory")
        print("  CONFIG_ID: 0-7")
        print("  REP_NUM: repetition number (1-N)")
        print("  BENCHMARK: benchmark name (e.g., 600.perlbench_s)")
        print("  WORKLOAD_TYPE: intspeed or fpspeed")
        sys.exit(1)
    
    config_id = args[0]
    rep_num = args[1]
    benchmark = args[2]
    workload_type = args[3]
    
    study_root, config = load_config()
    
    # Pin this monitoring script to non-benchmark cores to avoid interference
    # The benchmark itself will be pinned to TARGET_CORE via taskset
    target_core = int(config['TARGET_CORE'])
    total_cpus = os.cpu_count() or 1
    if total_cpus > 1:
        other_cores = {core for core in range(total_cpus) if core != target_core}
        if other_cores:
            os.sched_setaffinity(0, other_cores)
    
    success = run_benchmark(config_id, rep_num, benchmark, workload_type, study_root, config, run_dir)
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()
