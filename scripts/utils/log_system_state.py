#!/usr/bin/env python3
"""
Log System State for Diagnostics
Captures CPU frequency, MSR values, system load
Run with: python3 scripts/utils/log_system_state.py OUTPUT_FILE
"""

import os
import sys
import subprocess
from datetime import datetime
import glob

def get_cpu_frequencies():
    """Get current CPU frequencies"""
    freqs = []
    cpu_files = sorted(glob.glob('/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq'))
    for file in cpu_files:
        try:
            cpu_num = file.split('cpu')[1].split('/')[0]
            with open(file) as f:
                freq = f.read().strip()
            freqs.append(f"CPU {cpu_num}: {freq} kHz")
        except:
            pass
    return freqs

def get_cpu_governors():
    """Get current CPU governors"""
    govs = []
    gov_files = sorted(glob.glob('/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor'))
    for file in gov_files:
        try:
            cpu_num = file.split('cpu')[1].split('/')[0]
            with open(file) as f:
                gov = f.read().strip()
            govs.append(f"CPU {cpu_num}: {gov}")
        except:
            pass
    return govs

def get_msr_values():
    """Get MSR 0x1A4 values"""
    try:
        result = subprocess.run("rdmsr -a 0x1A4 2>/dev/null | head -8", 
                              shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip().split('\n')
        else:
            return ["rdmsr not available or cannot read MSR"]
    except:
        return ["Cannot read MSR"]

def get_system_load():
    """Get system load average"""
    try:
        result = subprocess.run("uptime", shell=True, capture_output=True, text=True)
        return result.stdout.strip()
    except:
        return "Cannot read system load"

def get_memory_info():
    """Get memory information"""
    try:
        result = subprocess.run("free -h", shell=True, capture_output=True, text=True)
        return result.stdout.strip()
    except:
        return "Cannot read memory info"

def get_thermal_info():
    """Get thermal information"""
    thermal_file = '/sys/class/thermal/thermal_zone0/temp'
    if os.path.exists(thermal_file):
        try:
            with open(thermal_file) as f:
                temp = int(f.read().strip()) // 1000
            return f"Thermal Zone 0: {temp}°C"
        except:
            return "Cannot read temperature"
    return "No thermal zone found"

def main():
    """Main function"""
    if len(sys.argv) < 2:
        print("Usage: python3 log_system_state.py OUTPUT_FILE")
        sys.exit(1)
    
    output_file = sys.argv[1]
    
    # Create output directory if needed
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Build log output
    log_lines = [
        "=== System State Log ===",
        f"Timestamp: {timestamp}",
        "",
        "--- CPU Frequencies ---",
    ]
    
    log_lines.extend(get_cpu_frequencies())
    
    log_lines.extend([
        "",
        "--- CPU Governors ---",
    ])
    
    log_lines.extend(get_cpu_governors())
    
    log_lines.extend([
        "",
        "--- MSR 0x1A4 (Prefetcher Control) ---",
    ])
    
    log_lines.extend(get_msr_values())
    
    log_lines.extend([
        "",
        "--- System Load ---",
        get_system_load(),
        "",
        "--- Memory Info ---",
    ])
    
    log_lines.extend(get_memory_info().split('\n'))
    
    log_lines.extend([
        "",
        "--- Thermal State ---",
        get_thermal_info(),
        "",
        "===========================",
        "",
    ])
    
    # Write to file
    try:
        with open(output_file, 'a') as f:
            f.write('\n'.join(log_lines))
    except Exception as e:
        print(f"Error writing to log file: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
