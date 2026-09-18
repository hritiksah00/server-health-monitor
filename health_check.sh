#!/bin/bash
# Monitor server health and write the results to a log file.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/server_health.log"
CURRENT_DATE=$(date '+%Y-%m-%d %H:%M:%S')

DISK_THRESHOLD=80
CPU_THRESHOLD=80
MEMORY_THRESHOLD=80
SERVICES=("docker")

HEALTH_STATUS=0

echo "--- Server Health Check: $CURRENT_DATE ---" >> "$LOG_FILE"

# 1. Check Root Disk Space
DISK_USAGE=$(df -P / | awk 'NR==2 {print $5}' | sed 's/%//')

if [ "$DISK_USAGE" -gt "$DISK_THRESHOLD" ]; then
    echo "WARNING: Disk usage is high at ${DISK_USAGE}%" >> "$LOG_FILE"
    HEALTH_STATUS=1
else
    echo "Disk space is healthy at ${DISK_USAGE}%" >> "$LOG_FILE"
fi

# 2. Check CPU Usage
CPU_USAGE=$(vmstat 1 2 | tail -1 | awk '{print 100 - $15}')

if [ "$CPU_USAGE" -gt "$CPU_THRESHOLD" ]; then
    echo "WARNING: CPU usage is high at ${CPU_USAGE}%" >> "$LOG_FILE"
    HEALTH_STATUS=1
else
    echo "CPU usage is healthy at ${CPU_USAGE}%" >> "$LOG_FILE"
fi

# 3. Check Memory Usage
MEMORY_USAGE=$(free | awk '/^Mem:/ {printf "%.0f", $3/$2 * 100}')

if [ "$MEMORY_USAGE" -gt "$MEMORY_THRESHOLD" ]; then
    echo "WARNING: Memory usage is high at ${MEMORY_USAGE}%" >> "$LOG_FILE"
    HEALTH_STATUS=1
else
    echo "Memory usage is healthy at ${MEMORY_USAGE}%" >> "$LOG_FILE"
fi

# 4. Check Monitored Services
for service in "${SERVICES[@]}"; do
    if ! systemctl cat "$service" >/dev/null 2>&1; then
        echo "Service Status: $service is NOT INSTALLED." >> "$LOG_FILE"
        HEALTH_STATUS=1
    elif systemctl is-active --quiet "$service"; then
        echo "Service Status: $service is RUNNING." >> "$LOG_FILE"
    else
        echo "CRITICAL ALERT: $service is DOWN!" >> "$LOG_FILE"
        HEALTH_STATUS=1
    fi
done

# 5. Write Overall Health Status
if [ "$HEALTH_STATUS" -eq 0 ]; then
    echo "Overall Status: HEALTHY" >> "$LOG_FILE"
else
    echo "Overall Status: ATTENTION REQUIRED" >> "$LOG_FILE"
fi

echo "------------------------------------------" >> "$LOG_FILE"

exit "$HEALTH_STATUS"