#!/usr/bin/env python3
"""
Set Prefetcher Configuration via MSR
Controls hardware prefetchers using MSR 0x1A4
Run with: sudo python3 scripts/setup/set_prefetchers.py CONFIG_ID
"""

import os
import sys
import subprocess
import configparser
from datetime import datetime

MSR_PREFETCH_CONTROL = "0x1A4"


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

def load_config():
    """Load configuration files"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    study_root = os.path.dirname(os.path.dirname(script_dir))
    
    config_file = os.path.join(study_root, 'configs', 'experiment_params.conf')
    prefetch_file = os.path.join(study_root, 'configs', 'prefetcher_configs.txt')
    
    if not os.path.exists(config_file):
        print(f"Error: Configuration file not found: {config_file}")
        sys.exit(1)
    
    if not os.path.exists(prefetch_file):
        print(f"Error: Prefetcher config file not found: {prefetch_file}")
        sys.exit(1)
    
    return study_root, config_file, prefetch_file


def setup_logging(study_root, config_id):
    """Initialize prefetcher configuration log"""
    log_dir = os.path.join(study_root, 'results', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(log_dir, f'set_prefetchers_config{config_id}_{timestamp}.log')

    # Open with line buffering (buffering=1) for immediate writes
    log_file = open(log_path, 'w', buffering=1)
    sys.stdout = TeeLogger(sys.stdout, log_file)
    sys.stderr = TeeLogger(sys.stderr, log_file)
    print(f"Logging to: {log_path}")
    return log_path

def parse_prefetch_config(prefetch_file, config_id):
    """Parse prefetcher configuration from file"""
    try:
        with open(prefetch_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('|')
                if len(parts) >= 3 and parts[0].strip() == str(config_id):
                    hex_value = parts[1].strip()
                    description = parts[2].strip()
                    return hex_value, description
    except Exception as e:
        print(f"Error reading prefetcher config: {e}")
        sys.exit(1)
    
    print(f"Error: Configuration ID {config_id} not found")
    sys.exit(1)

def check_root():
    """Verify script is running as root"""
    if os.geteuid() != 0:
        print("Error: This script must be run as root (use sudo)")
        sys.exit(1)

def run_msr_command(cmd, description):
    """Run MSR command and capture output"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            print(f"Error executing {description}")
            print(f"  Command: {cmd}")
            print(f"  Exit code: {result.returncode}")
            if stderr:
                print(f"  stderr: {stderr}")
            if stdout:
                print(f"  stdout: {stdout}")
            return None
        return result.stdout.strip()
    except Exception as e:
        print(f"Error: {e}")
        return None

def read_msr(msr_addr):
    """Read MSR value from all cores"""
    output = run_msr_command(f"rdmsr -a {msr_addr}", f"read MSR {msr_addr}")
    if output:
        return output.split('\n')[:4]  # Show first 4 cores
    return None

def extract_hex_value(msr_line):
    """Extract hex value from rdmsr output line."""
    line = msr_line.strip().lower()
    if ':' in line:
        line = line.split(':')[-1].strip()
    if line.startswith('0x'):
        line = line[2:]
    return line

def write_msr(msr_addr, value):
    """Write MSR value to all cores"""
    result = run_msr_command(f"wrmsr -a {msr_addr} {value}", f"write MSR {msr_addr}")
    if result is None:
        return False
    return True

def verify_msr(msr_addr, expected_hex):
    """Verify MSR was written correctly"""
    values = read_msr(msr_addr)
    if not values:
        return False
    
    expected_decimal = int(expected_hex, 16)
    all_match = True
    
    for val in values:
        parsed_val = extract_hex_value(val)
        actual_decimal = int(parsed_val, 16)
        if actual_decimal != expected_decimal:
            print(f"Warning: Core value mismatch - expected {expected_hex}, got 0x{parsed_val}")
            all_match = False
    
    return all_match

def main():
    """Main function"""
    if len(sys.argv) != 2:
        print("Usage: python3 set_prefetchers.py CONFIG_ID")
        print("  CONFIG_ID: 0-7 (see prefetcher_configs.txt)")
        sys.exit(1)
    
    try:
        config_id = int(sys.argv[1])
    except ValueError:
        print("Error: CONFIG_ID must be an integer")
        sys.exit(1)
    
    check_root()
    study_root, config_file, prefetch_file = load_config()
    
    # Pin this setup script to non-benchmark cores to avoid interference
    config_parser = configparser.ConfigParser()
    config_parser.read(config_file)
    target_core = int(config_parser.get('system', 'TARGET_CORE', fallback='0'))
    total_cpus = os.cpu_count() or 1
    if total_cpus > 1:
        other_cores = {core for core in range(total_cpus) if core != target_core}
        if other_cores:
            os.sched_setaffinity(0, other_cores)
    
    setup_logging(study_root, config_id)
    hex_value, description = parse_prefetch_config(prefetch_file, config_id)
    
    print("=========================================")
    print("Setting Prefetcher Configuration")
    print("=========================================")
    print(f"Config ID:   {config_id}")
    print(f"MSR Address: {MSR_PREFETCH_CONTROL}")
    print(f"Value:       {hex_value}")
    print(f"Description: {description}")
    print("")
    
    # Read current values
    print("Current MSR values:")
    current = read_msr(MSR_PREFETCH_CONTROL)
    if current:
        for val in current:
            print(f"  0x{val}")
    
    # Write new value
    print("")
    print("Writing new value to all cores...")
    if not write_msr(MSR_PREFETCH_CONTROL, hex_value):
        print("✗ Error: Failed to write MSR")
        sys.exit(1)
    
    # Verify
    print("")
    print("Verifying (reading back):")
    verify_values = read_msr(MSR_PREFETCH_CONTROL)
    if verify_values:
        for val in verify_values:
            print(f"  0x{val}")
    
    print("")
    if verify_msr(MSR_PREFETCH_CONTROL, hex_value):
        print("✓ Configuration applied successfully")
        print(f"  All cores set to {hex_value}")
        
        # Log the change
        try:
            changes_log = os.path.join(study_root, 'results', 'msr_changes.log')
            os.makedirs(os.path.dirname(changes_log), exist_ok=True)
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with open(changes_log, 'a') as f:
                f.write(f"{timestamp}|CONFIG_{config_id}|{hex_value}|{description}\n")
        except Exception as e:
            print(f"  Warning: Could not log change: {e}")
        
        sys.exit(0)
    else:
        print("✗ Error: MSR verification failed")
        print("  Not all cores were set correctly")
        sys.exit(1)

if __name__ == '__main__':
    main()
