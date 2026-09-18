# Linux Server Health Monitor

A lightweight Bash-based monitoring tool that checks essential Linux system resources and services, records timestamped results, and returns automation-friendly exit codes.

## Features

- Root disk usage monitoring
- CPU usage monitoring
- Memory usage monitoring
- Configurable `systemd` service checks
- Configurable warning thresholds
- Timestamped log output
- Overall health summary
- Standard exit codes for automation
- Cron-compatible execution
- Generated log files excluded from Git

## Requirements

- Linux with `systemd`
- Bash
- `procps` utilities:
  - `vmstat`
  - `free`
- Standard Linux utilities:
  - `df`
  - `awk`
  - `sed`
  - `tail`

## Project Structure

```text
server-health-monitor/
├── .gitignore
├── health_check.sh
└── README.md
```

Runtime logs are generated locally and are not committed to the repository.

## Configuration

The monitoring settings are located near the top of `health_check.sh`:

```bash
DISK_THRESHOLD=80
CPU_THRESHOLD=80
MEMORY_THRESHOLD=80
SERVICES=("docker")
```

The threshold values represent percentages.

Additional `systemd` services can be monitored by adding them to the array:

```bash
SERVICES=("docker" "ssh" "apache2")
```

Only add services that the system is expected to run.

## Usage

Clone the repository:

```bash
git clone https://github.com/hritiksah00/server-health-monitor.git
cd server-health-monitor
```

Make the script executable:

```bash
chmod +x health_check.sh
```

Check the Bash syntax:

```bash
bash -n health_check.sh
```

Run the monitor:

```bash
./health_check.sh
```

View the latest result:

```bash
tail -n 7 server_health.log
```

## Example Output

```text
--- Server Health Check: 2026-09-18 18:58:28 ---
Disk space is healthy at 13%
CPU usage is healthy at 3%
Memory usage is healthy at 49%
Service Status: docker is RUNNING.
Overall Status: HEALTHY
------------------------------------------
```

Actual values depend on the system being monitored.

## Exit Codes

| Exit code | Meaning |
|---:|---|
| `0` | All monitored resources and services are healthy |
| `1` | A threshold was exceeded or a required service needs attention |

Check the most recent exit code immediately after running the script:

```bash
./health_check.sh
echo $?
```

These exit codes allow cron jobs, CI/CD pipelines and other automation tools to detect failures.

## Automatic Execution with Cron

Find the absolute script path:

```bash
realpath health_check.sh
```

Open the current user’s crontab:

```bash
crontab -e
```

Example schedule for running the monitor every 30 minutes:

```cron
*/30 * * * * /absolute/path/to/health_check.sh >> /absolute/path/to/cron_output.log 2>&1
```

Replace both example paths with the actual path returned by `realpath`.

Verify the schedule:

```bash
crontab -l
```

Cron runs only while the Linux system is powered on and the cron service is active.

## Logging

The monitor writes its results to:

```text
server_health.log
```

Cron-related output can be redirected to:

```text
cron_output.log
```

Both files are excluded from Git through `.gitignore`.

## Skills Demonstrated

- Bash scripting
- Linux resource monitoring
- `systemd` service management
- Cron job automation
- Logging and exit-code handling
- Git branching and version control

## Author

Ritik Sah  
Cloud and DevOps Engineering Student