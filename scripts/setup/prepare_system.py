#!/usr/bin/env python3
"""
System Preparation for Benchmarking
Sets up essential prerequisites for accurate measurements
Run with: sudo python3 scripts/setup/prepare_system.py
"""

import os
import sys
import subprocess
import configparser
import datetime

# Setup logging
LOG_FILE = '/tmp/prepare_system.log'

def log_message(msg):
    """Write message to both stdout and log file"""
    print(msg)
    with open(LOG_FILE, 'a') as f:
        f.write(msg + '\n')

def setup_logging():
    """Initialize log file"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, 'w') as f:
        f.write(f"Prepare System Log - {timestamp}\n")
        f.write("=" * 50 + "\n\n")

def load_config():
    """Load configuration from experiment_params.conf"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    study_root = os.path.dirname(os.path.dirname(script_dir))
    config_file = os.path.join(study_root, 'configs', 'experiment_params.conf')
    
    if not os.path.exists(config_file):
        print(f"Error: Configuration file not found: {config_file}")
        sys.exit(1)
    
    config = configparser.ConfigParser()
    config.read(config_file)
    
    return {
        'TARGET_CORE': config.get('system', 'TARGET_CORE', fallback='0'),
        'CPU_FREQ': config.get('system', 'CPU_FREQ', fallback='2000MHz')
    }

def check_root():
    """Verify script is running as root"""
    if os.geteuid() != 0:
        log_message("Error: This script must be run as root (use sudo)")
        sys.exit(1)

def run_command(cmd, description, allow_fail=False):
    """Run shell command and handle errors"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0 and not allow_fail:
            log_message(f"✗ {description}: Failed")
            # Show actual error message
            if result.stderr.strip():
                log_message(f"  stderr: {result.stderr.strip()}")
            if result.stdout.strip():
                log_message(f"  stdout: {result.stdout.strip()}")
            # Log the command that failed
            log_message(f"  Command: {cmd}")
            log_message(f"  Exit code: {result.returncode}")
            return False
        return True
    except Exception as e:
        if not allow_fail:
            log_message(f"✗ {description}: {e}")
            return False
        return True

def load_msr_module():
    """Load MSR kernel module"""
    log_message("[1/3] Loading MSR module...")
    
    if run_command("modprobe msr", "Load MSR module"):
        if run_command("lsmod | grep -q msr", "Verify MSR loaded", allow_fail=True):
            log_message("  ✓ MSR module loaded")
            return True
    
    log_message("Error: Failed to load MSR module")
    sys.exit(1)

def disable_turbo_boost():
    """Disable CPU turbo boost"""
    log_message("[2/3] Disabling Turbo Boost...")
    
    intel_pstate_path = '/sys/devices/system/cpu/intel_pstate/no_turbo'
    cpufreq_path = '/sys/devices/system/cpu/cpufreq/boost'
    
    if os.path.exists(intel_pstate_path):
        try:
            with open(intel_pstate_path, 'w') as f:
                f.write('1')
            log_message("  ✓ Turbo disabled (intel_pstate)")
            return True
        except Exception as e:
            log_message(f"  ✗ Failed to disable turbo (intel_pstate): {e}")
            return False
    elif os.path.exists(cpufreq_path):
        try:
            with open(cpufreq_path, 'w') as f:
                f.write('0')
            log_message("  ✓ Turbo disabled (cpufreq)")
            return True
        except Exception as e:
            log_message(f"  ✗ Failed to disable turbo (cpufreq): {e}")
            return False
    else:
        log_message("  ✗ Warning: Could not disable turbo boost (no interface found)")
        return False

def lock_cpu_frequency(cpu_freq):
    """Lock CPU frequency to specified value"""
    log_message(f"[3/3] Locking CPU frequency to {cpu_freq}...")
    
    if not run_command(f"cpupower frequency-set -g performance", "Set performance governor"):
        log_message("  ✗ Failed to set performance governor")
        return False
    
    min_cmd = f"sudo cpupower frequency-set -d {cpu_freq}"
    max_cmd = f"sudo cpupower frequency-set -u {cpu_freq}"

    log_message(f"  Command: {min_cmd}")
    if not run_command(min_cmd, "Set minimum frequency"):
        log_message("  ✗ Failed to set minimum frequency")
        log_message("  Tip: Check available frequencies with: cpupower frequency-info")
        return False

    log_message(f"  Command: {max_cmd}")
    if not run_command(max_cmd, "Set maximum frequency"):
        log_message("  ✗ Failed to set maximum frequency")
        log_message("  Tip: Check available frequencies with: cpupower frequency-info")
        return False
    
    log_message("  ✓ Frequency locked")
    return True

def move_processes_off_target_core(target_core):
    """Move non-benchmark processes away from target core"""
    log_message(f"[4/4] Moving non-benchmark processes off core {target_core}...")

    total_cpus = os.cpu_count() or 1
    try:
        target_core_int = int(target_core)
    except ValueError:
        log_message(f"  ✗ Invalid TARGET_CORE value: {target_core}")
        return False

    other_cores = [str(core) for core in range(total_cpus) if core != target_core_int]
    if not other_cores:
        log_message("  ✗ Only one CPU core detected; cannot isolate benchmark core")
        return False

    core_list = ','.join(other_cores)
    result = subprocess.run("ps -e -o pid=", shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        log_message("  ✗ Failed to list running processes")
        return False

    skip_pids = {0, 1, os.getpid(), os.getppid()}
    moved = 0
    attempted = 0

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue

        if pid in skip_pids:
            continue

        attempted += 1
        set_result = subprocess.run(
            f"taskset -pc {core_list} {pid}",
            shell=True,
            capture_output=True,
            text=True
        )
        if set_result.returncode == 0:
            moved += 1

    log_message(f"  ✓ Moved {moved}/{attempted} processes to cores: {core_list}")
    if moved < attempted:
        log_message("  Note: Some kernel/system processes could not be moved (expected)")
    return True

def verify_settings(target_core):
    """Verify system settings applied correctly"""
    log_message("\nVerification:")
    
    governor_file = f'/sys/devices/system/cpu/cpu{target_core}/cpufreq/scaling_governor'
    freq_file = f'/sys/devices/system/cpu/cpu{target_core}/cpufreq/scaling_cur_freq'
    turbo_file = '/sys/devices/system/cpu/intel_pstate/no_turbo'
    
    try:
        with open(governor_file) as f:
            gov = f.read().strip()
        log_message(f"  CPU Governor: {gov}")
    except:
        log_message("  CPU Governor: <Could not read>")
    
    try:
        with open(freq_file) as f:
            freq = f.read().strip()
        log_message(f"  CPU Frequency: {freq} kHz")
    except:
        log_message("  CPU Frequency: <Could not read>")
    
    if os.path.exists(turbo_file):
        try:
            with open(turbo_file) as f:
                turbo = f.read().strip()
            log_message(f"  Turbo Boost: {'Disabled' if turbo == '1' else 'Enabled'}")
        except:
            log_message("  Turbo Boost: <Could not read>")

def enable_turbo_boost():
    """Enable CPU turbo boost"""
    log_message("[1/3] Enabling Turbo Boost...")
    
    intel_pstate_path = '/sys/devices/system/cpu/intel_pstate/no_turbo'
    cpufreq_path = '/sys/devices/system/cpu/cpufreq/boost'
    
    if os.path.exists(intel_pstate_path):
        try:
            with open(intel_pstate_path, 'w') as f:
                f.write('0')
            log_message("  ✓ Turbo enabled (intel_pstate)")
            return True
        except Exception as e:
            log_message(f"  ✗ Failed to enable turbo (intel_pstate): {e}")
            return False
    elif os.path.exists(cpufreq_path):
        try:
            with open(cpufreq_path, 'w') as f:
                f.write('1')
            log_message("  ✓ Turbo enabled (cpufreq)")
            return True
        except Exception as e:
            log_message(f"  ✗ Failed to enable turbo (cpufreq): {e}")
            return False
    else:
        log_message("  ✗ Warning: Could not enable turbo boost (no interface found)")
        return False

def reset_cpu_frequency():
    """Reset CPU frequency to powersave governor"""
    log_message("[2/3] Resetting CPU frequency...")
    
    if not run_command(f"sudo cpupower frequency-set -g powersave", "Set powersave governor"):
        log_message("  Warning: Failed to reset to powersave governor")
        return False
    
    log_message("  ✓ Frequency reset to dynamic scaling")
    return True

def restore_process_affinity():
    """Restore process CPU affinity to all cores"""
    log_message("[3/3] Restoring process CPU affinity...")

    total_cpus = os.cpu_count() or 1
    core_list = ','.join(str(core) for core in range(total_cpus))
    result = subprocess.run("ps -e -o pid=", shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        log_message("  ✗ Failed to list running processes")
        return False

    skip_pids = {0, 1, os.getpid(), os.getppid()}
    restored = 0
    attempted = 0

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue

        if pid in skip_pids:
            continue

        attempted += 1
        set_result = subprocess.run(
            f"taskset -pc {core_list} {pid}",
            shell=True,
            capture_output=True,
            text=True
        )
        if set_result.returncode == 0:
            restored += 1

    log_message(f"  ✓ Restored affinity for {restored}/{attempted} processes")
    if restored < attempted:
        log_message("  Note: Some kernel/system processes could not be updated (expected)")
    return True

def reset_system():
    """Reset system to default state after benchmarking"""
    log_message("=========================================")
    log_message("Restoring system to default state")
    log_message("=========================================")
    log_message("")
    
    enable_turbo_boost()
    reset_cpu_frequency()
    restore_process_affinity()
    
    log_message("")
    verify_settings('0')
    
    log_message("")
    log_message("=========================================")
    log_message("System reset complete")
    log_message("=========================================")
    log_message("")
    log_message(f"📋 Full log saved to: {LOG_FILE}")

def main():
    """Main function"""
    setup_logging()
    check_root()
    
    # Check for command line arguments
    if len(sys.argv) > 1 and sys.argv[1] == '--reset':
        reset_system()
        return
    
    # Default: prepare system for benchmarking
    log_message("=========================================")
    log_message("Preparing system for benchmarking")
    log_message("=========================================")
    
    config = load_config()
    target_core = config['TARGET_CORE']
    cpu_freq = config['CPU_FREQ']
    
    log_message("Essential setup for reproducible benchmarks:")
    log_message("  - MSR module (prefetcher control)")
    log_message("  - Fixed CPU frequency (no dynamic scaling)")
    log_message("  - Performance governor")
    log_message("  - Benchmark core isolation")
    log_message("")
    
    # Run setup steps
    load_msr_module()
    disable_turbo_boost()
    lock_cpu_frequency(cpu_freq)
    move_processes_off_target_core(target_core)
    
    log_message("")
    verify_settings(target_core)
    
    log_message("")
    log_message("=========================================")
    log_message("System ready for benchmarking")
    log_message("=========================================")
    log_message("")
    log_message(f"📋 Full log saved to: {LOG_FILE}")
    log_message("Usage: sudo python3 scripts/setup/prepare_system.py [--reset]")
    log_message("  (no args) = Prepare system for benchmarking")
    log_message("  --reset   = Restore system to default state")

if __name__ == '__main__':
    main()
