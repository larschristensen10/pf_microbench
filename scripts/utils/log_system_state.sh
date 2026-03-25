#!/bin/bash
# Log System State for Diagnostics
# Captures CPU frequency, MSR values, system load
# Usage: ./log_system_state.sh OUTPUT_FILE

if [ $# -lt 1 ]; then
    echo "Usage: $0 OUTPUT_FILE"
    exit 1
fi

OUTPUT_FILE=$1
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

{
    echo "=== System State Log ==="
    echo "Timestamp: $TIMESTAMP"
    echo ""
    
    echo "--- CPU Frequencies ---"
    for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; do
        if [ -f "$cpu" ]; then
            CPU_NUM=$(echo "$cpu" | grep -oP 'cpu\K[0-9]+')
            FREQ=$(cat "$cpu")
            echo "CPU $CPU_NUM: $FREQ kHz"
        fi
    done
    echo ""
    
    echo "--- CPU Governors ---"
    for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        if [ -f "$cpu" ]; then
            CPU_NUM=$(echo "$cpu" | grep -oP 'cpu\K[0-9]+')
            GOV=$(cat "$cpu")
            echo "CPU $CPU_NUM: $GOV"
        fi
    done
    echo ""
    
    echo "--- MSR 0x1A4 (Prefetcher Control) ---"
    if command -v rdmsr &> /dev/null; then
        sudo rdmsr -a 0x1A4 2>/dev/null | head -8 || echo "Cannot read MSR"
    else
        echo "rdmsr not available"
    fi
    echo ""
    
    echo "--- System Load ---"
    uptime
    echo ""
    
    echo "--- Memory Info ---"
    free -h
    echo ""
    
    echo "--- Thermal State ---"
    if [ -f /sys/class/thermal/thermal_zone0/temp ]; then
        TEMP=$(cat /sys/class/thermal/thermal_zone0/temp)
        echo "Thermal Zone 0: $((TEMP / 1000))°C"
    fi
    
    echo ""
    echo "==========================="
    
} >> "$OUTPUT_FILE"

exit 0
