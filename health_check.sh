#!/bin/bash
# A simple script to monitor server health and log it.

LOG_FILE="./server_health.log"
CURRENT_DATE=$(date '+%Y-%m-%d %H:%M:%S')

echo "--- Server Health Check: $CURRENT_DATE ---" >> $LOG_FILE

# 1. Check Root Disk Space
# This gets the percentage of disk used on the root partition
DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')

if [ "$DISK_USAGE" -gt 80 ]; then
    echo "WARNING: Disk usage is high at ${DISK_USAGE}%" >> $LOG_FILE
else
    echo "Disk space is healthy at ${DISK_USAGE}%" >> $LOG_FILE
fi

# 2. Check if the Web Server is Running (e.g., Apache)
if systemctl is-active --quiet apache2; then
    echo "Service Status: Apache Web Server is RUNNING." >> $LOG_FILE
else
    echo "CRITICAL ALERT: Apache Web Server is DOWN!" >> $LOG_FILE
fi

echo "------------------------------------------" >> $LOG_FILE
