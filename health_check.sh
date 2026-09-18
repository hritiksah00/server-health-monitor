#!/bin/bash
# Monitor server health and write the results to a log file.

LOG_FILE="./server_health.log"
CURRENT_DATE=$(date '+%Y-%m-%d %H:%M:%S')

DISK_THRESHOLD=80
CPU_THRESHOLD=80
MEMORY_THRESHOLD=80

echo "--- Server Health Check: $CURRENT_DATE ---" >> "$LOG_FILE"

# 1. Check Root Disk Space
DISK_USAGE=$(df -P / | awk 'NR==2 {print $5}' | sed 's/%//')

if [ "$DISK_USAGE" -gt "$DISK_THRESHOLD" ]; then
    echo "WARNING: Disk usage is high at ${DISK_USAGE}%" >> "$LOG_FILE"
else
    echo "Disk space is healthy at ${DISK_USAGE}%" >> "$LOG_FILE"
fi

# 2. Check CPU Usage
CPU_USAGE=$(vmstat 1 2 | tail -1 | awk '{print 100 - $15}')

if [ "$CPU_USAGE" -gt "$CPU_THRESHOLD" ]; then
    echo "WARNING: CPU usage is high at ${CPU_USAGE}%" >> "$LOG_FILE"
else
    echo "CPU usage is healthy at ${CPU_USAGE}%" >> "$LOG_FILE"
fi

# 3. Check Memory Usage
MEMORY_USAGE=$(free | awk '/^Mem:/ {printf "%.0f", $3/$2 * 100}')

if [ "$MEMORY_USAGE" -gt "$MEMORY_THRESHOLD" ]; then
    echo "WARNING: Memory usage is high at ${MEMORY_USAGE}%" >> "$LOG_FILE"
else
    echo "Memory usage is healthy at ${MEMORY_USAGE}%" >> "$LOG_FILE"
fi

# 4. Check Whether Apache Is Running
if systemctl is-active --quiet apache2; then
    echo "Service Status: Apache Web Server is RUNNING." >> "$LOG_FILE"
else
    echo "CRITICAL ALERT: Apache Web Server is DOWN!" >> "$LOG_FILE"
fi

echo "------------------------------------------" >> "$LOG_FILE"