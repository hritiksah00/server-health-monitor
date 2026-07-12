# Server Health Monitor

A lightweight Bash script designed to automate routine server health checks. This script monitors root disk space and verifies the active status of essential web services, logging all outputs for easy review.

## Features
* **Disk Space Monitoring:** Checks the root partition and triggers a warning if usage exceeds 80%.
* **Service Verification:** Uses `systemctl` to check if the Apache web server is running.
* **Automated Logging:** Appends timestamped results to a dedicated log file.

## Prerequisites
* A Linux environment (Ubuntu/Debian preferred).
* `systemd` for service checking.

## How to Run
1. Make the script executable:
   `chmod +x health_check.sh`
2. Run the script manually:
   `./health_check.sh`
3. Check the logs:
   `cat /var/log/server_health.log` *(Note: requires sudo privileges to write to /var/log)*