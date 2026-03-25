#!/usr/bin/env python3
"""
Cleanup System State Between Benchmark Runs
Drops caches and ensures clean architectural state
Run with: sudo python3 scripts/utils/cleanup_state.py
"""

import os
import sys
import time

def drop_caches():
    """Drop filesystem caches"""
    try:
        # Sync filesystems
        os.system('sync')
        
        # Drop page cache, dentries, and inodes
        with open('/proc/sys/vm/drop_caches', 'w') as f:
            f.write('3')
        return True
    except Exception as e:
        print(f"Warning: Could not drop caches: {e}")
        return False

def main():
    """Main function"""
    if not drop_caches():
        print("Warning: Caches may not have been fully dropped (need root privileges)")
    
    # Short sleep to let system settle
    time.sleep(2)

if __name__ == '__main__':
    main()
