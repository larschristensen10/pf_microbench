#!/usr/bin/env python3
"""
Environment Verification Script
Validates all prerequisites before experiment execution
Run with: python3 scripts/setup/verify_environment.py
"""

import os
import sys
import subprocess
import configparser
import shutil

class Colors:
    """ANSI color codes"""
    GREEN = '\033[0;32m'
    RED = '\033[0;31m'
    YELLOW = '\033[1;33m'
    END = '\033[0m'

def load_config(study_root):
    """Load configuration"""
    config_file = os.path.join(study_root, 'configs', 'experiment_params.conf')
    
    if not os.path.exists(config_file):
        print(f"Error: Configuration file not found: {config_file}")
        sys.exit(1)
    
    config = configparser.ConfigParser()
    config.read(config_file)
    
    return {
        'SPEC_ROOT': config.get('spec', 'SPEC_ROOT', fallback='/home/kc/benchmarks/SPEC17-Working'),
        'SPEC_CONFIG': config.get('spec', 'SPEC_CONFIG', fallback='Alderlake-gcc-linux-x86.cfg'),
    }

def check(condition, description):
    """Print check result"""
    if condition:
        print(f"{Colors.GREEN}✓{Colors.END} {description}")
        return True
    else:
        print(f"{Colors.RED}✗{Colors.END} {description}")
        return False

def warn(description):
    """Print warning"""
    print(f"{Colors.YELLOW}⚠{Colors.END} {description}")

def main():
    """Main function"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    study_root = os.path.dirname(os.path.dirname(script_dir))
    
    config = load_config(study_root)
    spec_root = config['SPEC_ROOT']
    spec_config = config['SPEC_CONFIG']
    
    errors = 0
    warnings = 0
    
    print("=========================================")
    print("Environment Verification")
    print("=========================================")
    
    # Check 1: SPEC installation
    if check(os.path.isdir(spec_root), "SPEC CPU2017 installation"):
        if not check(os.path.exists(os.path.join(spec_root, 'bin', 'runcpu')), 
                    "  SPEC runcpu executable found"):
            errors += 1
    else:
        errors += 1
    
    # Check 2: SPEC config file
    config_path = os.path.join(spec_root, 'config', spec_config)
    if not check(os.path.exists(config_path), f"SPEC config file ({spec_config})"):
        errors += 1
    
    # Check 3: Benchmarks compiled
    int_benchmarks = ['600.perlbench_s', '620.omnetpp_s', '625.x264_s']
    fp_benchmarks = ['621.wrf_s', '627.cam4_s', '628.pop2_s']
    
    unbuilt_int = []
    unbuilt_fp = []
    
    for bmk in int_benchmarks:
        exe_dir = os.path.join(spec_root, 'benchspec', 'CPU', bmk, 'exe')
        if not os.path.exists(exe_dir) or not os.listdir(exe_dir):
            unbuilt_int.append(bmk)
    
    for bmk in fp_benchmarks:
        exe_dir = os.path.join(spec_root, 'benchspec', 'CPU', bmk, 'exe')
        if not os.path.exists(exe_dir) or not os.listdir(exe_dir):
            unbuilt_fp.append(bmk)
    
    if not unbuilt_int and not unbuilt_fp:
        check(True, "SPEC benchmarks compiled")
    else:
        if unbuilt_int or unbuilt_fp:
            warn(f"Some benchmarks not built:")
            if unbuilt_int:
                print(f"    Integer: {', '.join(unbuilt_int)}")
            if unbuilt_fp:
                print(f"    FP: {', '.join(unbuilt_fp)}")
            print(f"    Fix: runcpu --config=alderlake.cfg --action=build intspeed fpspeed")
            warnings += 1
    
    # Check 4: MSR module
    result = subprocess.run("lsmod | grep msr", shell=True, capture_output=True)
    if not check(result.returncode == 0, "MSR module loaded"):
        print("  Hint: Run 'sudo modprobe msr'")
        errors += 1
    
    # Check 5: MSR read access
    if shutil.which('rdmsr'):
        result = subprocess.run("sudo rdmsr -a 0x1A4", shell=True, capture_output=True)
        if not check(result.returncode == 0, "MSR read access (rdmsr)"):
            print("  Hint: Run 'sudo python3 scripts/setup/prepare_system.py'")
            errors += 1
    else:
        warn("rdmsr not installed (apt install msr-tools)")
        print("  Hint: Install with 'apt install msr-tools'")
        warnings += 1
    
    # Check 6: perf installed
    if not check(shutil.which('perf') is not None, "perf tool installed"):
        print("  Hint: Install with 'apt install linux-tools-generic'")
        errors += 1
    
    # Check 7: cpupower installed
    if not check(shutil.which('cpupower') is not None, "cpupower tool installed"):
        print("  Hint: Install with 'apt install linux-cpupower'")
        errors += 1
    
    # Check 8: Python dependencies
    try:
        import numpy
        check(True, "numpy installed")
    except ImportError:
        warn("numpy not installed")
        print("  Hint: pip install numpy")
        warnings += 1
    
    try:
        import pandas
        check(True, "pandas installed")
    except ImportError:
        warn("pandas not installed")
        print("  Hint: pip install pandas")
        warnings += 1
    
    try:
        import matplotlib
        check(True, "matplotlib installed")
    except ImportError:
        warn("matplotlib not installed")
        print("  Hint: pip install matplotlib")
        warnings += 1
    
    # Check 9: Disk space
    stat = shutil.disk_usage(study_root)
    available_gb = stat.free / (1024**3)
    if not check(available_gb > 50, f"Adequate disk space ({available_gb:.1f} GB available)"):
        warn(f"Recommended: 100+ GB free (have {available_gb:.1f} GB)")
        warnings += 1
    
    # Check 10: Results directory writable
    results_dir = os.path.join(study_root, 'results')
    os.makedirs(results_dir, exist_ok=True)
    if not check(os.access(results_dir, os.W_OK), "Results directory writable"):
        errors += 1
    
    print("")
    print("=========================================")
    print(f"✓ Errors: {errors} | ⚠ Warnings: {warnings}")
    print("=========================================")
    
    if errors > 0:
        print(f"\n{Colors.RED}❌ Environment verification FAILED{Colors.END}")
        print("Fix errors above before running experiments")
        sys.exit(1)
    elif warnings > 0:
        print(f"\n{Colors.YELLOW}⚠ Environment verification PASSED (with {warnings} warnings){Colors.END}")
        print("Optional: Address warnings above for better results")
        sys.exit(0)
    else:
        print(f"\n{Colors.GREEN}✅ Environment verification PASSED{Colors.END}")
        print("Ready to run experiments")
        sys.exit(0)

if __name__ == '__main__':
    main()
