#!/usr/bin/env python3
"""
Master Script for SPEC CPU2017 Benchmark Sweep
Runs all benchmarks (intspeed or fpspeed) across all prefetcher configurations

Run with:
  sudo python3 scripts/execution/run_benchmark_sweep.py intspeed
  sudo python3 scripts/execution/run_benchmark_sweep.py fpspeed [run_name]
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


def setup_logging(study_root, run_dir, workload_type):
    """Initialize session log for benchmark sweep"""
    try:
        log_dir = os.path.join(run_dir, 'logs')
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f'run_{workload_type}_sweep.log')

        # Open with line buffering (buffering=1) for immediate writes
        log_file = open(log_path, 'w', buffering=1)
        sys.stdout = TeeLogger(sys.stdout, log_file)
        sys.stderr = TeeLogger(sys.stderr, log_file)

        print(f"Logging to: {log_path}")
        return log_path
    except Exception as e:
        print(f"FATAL ERROR: Could not setup logging in {run_dir}")
        print(f"Error: {e}")
        print(f"Aborting to prevent writing logs to wrong location")
        sys.exit(1)

def load_config(study_root):
    """Load configuration"""
    config_file = os.path.join(study_root, 'configs', 'experiment_params.conf')
    prefetch_file = os.path.join(study_root, 'configs', 'prefetcher_configs.txt')
    
    if not os.path.exists(config_file):
        print(f"Error: Configuration file not found: {config_file}")
        sys.exit(1)
    
    config = configparser.ConfigParser()
    config.read(config_file)
    
    # Load active configuration IDs from prefetcher config file
    active_config_ids = []
    try:
        with open(prefetch_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('|', 1)
                if not parts:
                    continue
                try:
                    active_config_ids.append(int(parts[0].strip()))
                except ValueError:
                    continue
    except:
        active_config_ids = list(range(8))

    if not active_config_ids:
        active_config_ids = list(range(8))
    
    return {
        'TARGET_CORE': config.get('system', 'TARGET_CORE', fallback='0'),
        'REPETITIONS': int(config.get('experiment', 'REPETITIONS', fallback='10')),
        'CONFIG_IDS': active_config_ids,
        'NUM_CONFIGS': len(active_config_ids),
    }, prefetch_file

def verify_environment(study_root):
    """Verify environment"""
    setup_dir = os.path.join(study_root, 'scripts', 'setup')
    script = os.path.join(setup_dir, 'verify_environment.py')
    
    print("Verifying environment...")
    result = subprocess.run(['python3', script])
    
    if result.returncode != 0:
        print("")
        print("Environment verification failed!")
        print("Run 'sudo python3 scripts/setup/prepare_system.py' first")
        sys.exit(1)
    print()

def prepare_system(study_root):
    """Prepare system"""
    setup_dir = os.path.join(study_root, 'scripts', 'setup')
    script = os.path.join(setup_dir, 'prepare_system.py')
    
    print("Preparing system...")
    result = subprocess.run(['sudo', 'python3', script])
    
    if result.returncode != 0:
        print("System preparation may have failed")
    print()

def run_single_config(workload_type, config_id, study_root, run_dir):
    """Run single configuration"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(script_dir, 'run_single_config.py')
    
    # Run without capturing output so it streams in real-time to our redirected stdout/stderr
    result = subprocess.run(['python3', script, '--run-dir', run_dir, workload_type, str(config_id)],
                          stdout=None, stderr=None)  # Don't capture, let it stream
    sys.stdout.flush()  # Ensure output is flushed
    return result.returncode == 0

def main():
    """Main function"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    study_root = os.path.dirname(os.path.dirname(script_dir))
    
    # Parse workload type
    if len(sys.argv) < 2:
        print("Usage: python3 run_benchmark_sweep.py <workload> [run_name]")
        print("  workload: intspeed or fpspeed")
        print("  run_name: Optional identifier for this run")
        sys.exit(1)
    
    workload_type = sys.argv[1].lower()
    if workload_type not in ['intspeed', 'fpspeed']:
        print(f"Error: Unknown workload '{workload_type}'")
        print("Supported: intspeed, fpspeed")
        sys.exit(1)
    
    # Extract run_name (skip past workload type and any flags)
    run_name = ""
    if len(sys.argv) > 2:
        run_name = sys.argv[2]
        if run_name.startswith('-'):
            run_name = ""
    
    # Create per-run directory with timestamp (and optional run name)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir_name = f"{timestamp}_{run_name}" if run_name else timestamp
    run_dir = os.path.join(study_root, 'results', 'runs', run_dir_name)
    
    try:
        os.makedirs(run_dir, exist_ok=True)
    except Exception as e:
        print(f"FATAL ERROR: Could not create run directory: {run_dir}")
        print(f"Error: {e}")
        print(f"Aborting to prevent writing logs to wrong location")
        sys.exit(1)

    # Setup logging AFTER run_dir is confirmed to exist
    log_path = setup_logging(study_root, run_dir, workload_type)
    exit_code = 0
    
    try:
        # Workload-specific labels and timing
        if workload_type == 'intspeed':
            workload_label = "Integer Speed"
            estimated_time = "40-80 hours"
        else:  # fpspeed
            workload_label = "Floating-Point Speed"
            estimated_time = "80-240 hours"
        
        print("=========================================")
        print(f"SPEC CPU2017 {workload_label} Sweep")
        print("=========================================")
        print(f"Study root: {study_root}")
        print(f"Run directory: {run_dir}")
        
        config, prefetch_file = load_config(study_root)
        print(f"Target core: {config['TARGET_CORE']}")
        print(f"Repetitions per config: {config['REPETITIONS']}")
        print(f"Configurations: {config['NUM_CONFIGS']}")
        print(f"Active config IDs: {config['CONFIG_IDS']}")
        print()
        
        # Pin orchestration script to non-benchmark cores to avoid interference
        target_core = int(config['TARGET_CORE'])
        total_cpus = os.cpu_count() or 1
        if total_cpus > 1:
            other_cores = {core for core in range(total_cpus) if core != target_core}
            if other_cores:
                os.sched_setaffinity(0, other_cores)
                print(f"Pinned orchestration to cores: {sorted(other_cores)}")
                print(f"Benchmark core {target_core} isolated from Python runtime")
                print()
        
        # Verify environment
        verify_environment(study_root)
        
        # Ask for confirmation
        print(f"Ready to run full {workload_type} sweep")
        print(f"Estimated time: {estimated_time} (depends on system)")
        response = input("Continue? (yes/no): ").strip().lower()
        
        if response not in ['yes', 'y']:
            print("Cancelled")
            exit_code = 0
            return
        print()
        
        # Prepare system
        prepare_system(study_root)
        print()
        
        # Run all configurations
        successful_configs = 0
        failed_configs = 0
        
        for idx, config_id in enumerate(config['CONFIG_IDS']):
            print(f"\n{'='*40}")
            print(f"Configuration {idx + 1}/{config['NUM_CONFIGS']} (ID {config_id})")
            print(f"{'='*40}\n")
            
            if run_single_config(workload_type, config_id, study_root, run_dir):
                successful_configs += 1
            else:
                failed_configs += 1
                print(f"Configuration {config_id} had failures")
        
        # Summary
        print("")
        print("=========================================")
        print(f"{workload_label} Sweep Complete")
        print("=========================================")
        print(f"Configurations run: {config['NUM_CONFIGS']}")
        print(f"Successful: {successful_configs}")
        print(f"Failed: {failed_configs}")
        print("")
        print("Next steps:")
        print(f"  python3 analysis/aggregate_data.py {workload_type}")
        print(f"  python3 analysis/compute_statistics.py {workload_type}")
        print(f"  python3 analysis/generate_plots.py {workload_type}")
        
        exit_code = 0 if failed_configs == 0 else 1
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        exit_code = 130
    finally:
        print("")
        print(f"Final log location: {log_path}")
        print(f"Run directory: {run_dir}")
        
        # Fix permissions so files aren't root-only (run with sudo)
        try:
            os.system(f"sudo chown -R $(whoami):$(whoami) {run_dir} 2>/dev/null")
            os.system(f"chmod -R u+rw {run_dir} 2>/dev/null")
        except:
            pass

    sys.exit(exit_code)

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] in ['-h', '--help']:
        print("Usage: python3 run_benchmark_sweep.py <workload> [run_name]")
        print()
        print("Arguments:")
        print("  workload:  intspeed or fpspeed (required)")
        print("  run_name:  Optional identifier for this run")
        print()
        print("Examples:")
        print("  sudo python3 run_benchmark_sweep.py intspeed")
        print("  sudo python3 run_benchmark_sweep.py fpspeed baseline")
        print()
        print("Results stored in: results/runs/{timestamp}_{run_name}/")
        sys.exit(0)
    main()
