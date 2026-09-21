# Architecture and operating model

The original Bash script measured a few resources during a manual run. This version keeps its entry point and adds an installed lifecycle: configuration, identity, scheduling, durable results, verified backup retention, and recovery.

## Component boundaries

| Component | Responsibility | Explicit boundary |
| --- | --- | --- |
| Root-run installer | Create the service account/group, install files and units, enable schedules | Does not edit SSH/UFW; preserves existing configuration |
| Monitor job | Measure host resources, query configured units, check backup freshness | Detects and records; does not restart the host's services |
| Backup job | Archive dedicated lab data, verify, publish, then retain valid copies | No database snapshot, remote replication, or encryption |
| Restore command | Verify digest and safe members; extract into a new destination | Never restores directly over the live source |
| Failure handler | Journal notification plus structured event when config/logs are usable | Local alert only |
| Operator group | Read the lab's configuration, logs, and backups | No implicit sudo rights and no write permission to code/config |
| SSH/UFW lab | Explicit host access policy and management network rule | Applied separately from monitoring installation |

## Filesystem permissions

| Location | Owner / group | Mode | Reason |
| --- | --- | --- | --- |
| `/opt/linux-admin/linux_admin.py` | root / root | 0755 | Service cannot replace its executable |
| `/etc/linux-admin/config.json` | root / linux-admin | 0640 | Administrator writes; service/operators read |
| `/srv/linux-admin/data` | root / linux-admin | 2750 | Group can read; setgid preserves the intended group |
| Sample source files | root / linux-admin | 0640 | Backup process reads but cannot change source |
| Log/state/backup directories | linux-admin / linux-admin | 0750 | Service writes, operators inspect |
| JSON logs, snapshots, archives/checksums | linux-admin / linux-admin | 0640 | No access for unrelated users |
| Restored directories/files | restoring user | 0700 / 0600 | Deliberate review before returning data to service |

The service user has `/usr/sbin/nologin` and no sudo rules. Operators in the same group can read backup contents; only add trusted lab operators.

Systemd adds `NoNewPrivileges`, a read-only system view, protected home directories, and a small writable-path allowlist. Jobs need local UNIX sockets for systemd queries but no network access. These controls supplement normal Unix permissions.

## Measurement semantics

CPU is a sampled counter delta, not an instantaneous reading or load average. RAM uses available memory rather than only free memory. Disk checks operate on the filesystem containing each configured path. Query failures receive an error state instead of becoming empty “healthy” measurements.

The monitor overwrites `status.json` atomically after collecting checks. `report` prints this saved snapshot: inspect its timestamp, especially when troubleshooting a failed or disabled timer. Detailed events append to `operations.jsonl` and stdout, which systemd captures in the journal.

## Backup lifecycle

1. Obtain an exclusive lock shared with restore operations.
2. Validate the source, reject links/special files, enforce the size/file-count limit, and check free space.
3. Write an archive under a temporary name and inspect its members.
4. Calculate SHA-256 and publish the checksum followed by the final archive name.
5. Verify the completed pair and write the success record.
6. Verify existing candidates, then remove only surplus valid backups.

A failed run before publication leaves no completed new archive. A process killed during a write can leave a `.pending-*` temporary file; it is excluded from backup discovery and retention. Inspect and remove stale temporary files only after confirming no backup is running.

The source must be trusted and quiescent. This is not a filesystem snapshot: stop writers before backing up changing application files. Keep a separately managed off-device copy if the data matters.

## Scheduling decisions

The monitor uses a monotonic interval after its previous invocation ends. It does not replay every missed minute after shutdown. The backup uses a daily calendar timer with `Persistent=true` so a missed daily run can be caught up at activation, plus a short randomized delay.

The backup time uses the VM's timezone. Check `timedatectl`. Systemd does not launch another instance of an already active oneshot unit; filesystem locks additionally cover manual invocations.

A warning or error makes the monitor job fail, which starts the alert template. The timer stays available for the next scheduled attempt. The alert first writes through `logger`, so an invalid project configuration does not erase the notification; structured logging may also fail if that configuration or its log destination is unusable.

## Dependencies and cost

Python standard library, Bash, systemd, logrotate, OpenSSH, and UFW. No API keys, SaaS account, external alert service, or AWS resources are required. Existing local hardware and normal internet access are sufficient for this lab.
