# Operations, troubleshooting, and recovery

Run installed-mode commands on the lab VM. To inspect local mode, add `--config config/local.json` and use `python3 linux_admin.py`.

## Daily inspection

```bash
systemctl list-timers 'linux-admin-*' --all
sudo -u linux-admin /usr/local/bin/linux-admin report
sudo journalctl -u linux-admin-monitor.service -u linux-admin-backup.service -n 40 --no-pager
sudo tail -n 5 /var/log/linux-admin/operations.jsonl
```

A successful oneshot job normally returns to `inactive (dead)` when finished. Inspect its `Result` and `ExecMainStatus`; it is not a continuously running daemon.

```bash
systemctl show linux-admin-monitor.service --property=Result,ExecMainStatus
```

Always inspect the saved report's timestamp. A stale “ok” snapshot is not evidence that a disabled or failed timer is still collecting data.

## Symptom guide

| Symptom | Diagnosis | Recovery |
| --- | --- | --- |
| Monitor exits 1 | Inspect checks in status JSON; identify threshold, inactive unit, or overdue backup | Fix the specific condition; run the monitor again |
| Monitor exits 2 | Invalid config, unavailable metrics/systemd, permission or logging error | Check journal/stderr and validate config; do not treat it as a health result |
| Operation exits 3 | Another invocation holds the monitor or backup lock | Inspect active jobs and wait; never delete the lock file to bypass the holder |
| Missing service | Wrong unit name or service is not installed/expected | Use `systemctl list-unit-files`; correct the explicit list |
| SSH is inactive | Ubuntu may use socket activation | Check `ssh.socket`; only monitor units expected to stay active |
| Permission denied | Wrong ownership/group membership or systemd write allowlist | Use `namei -l PATH` and `stat PATH`; correct the specific path |
| No timer runs | Timer disabled, host asleep, or invalid timer config | Inspect `list-timers` and journal; check timezone and enable the relevant timer |
| Old logs keep growing | logrotate schedule disabled or policy error | Run `sudo logrotate --debug /etc/logrotate.d/linux-admin` |
| Backup missing/stale | Backup job failed, timer disabled, unreadable source, or full destination | Inspect backup journal before manually starting it |
| Checksum mismatch | Archive or sidecar changed/corrupted | Do not restore it; choose an older verified copy and investigate storage |
| Restore target exists | Restore intentionally refuses overwrite | Select a new destination; compare recovered data before replacing anything |
| Source exceeds 100 MiB | Default lab limit prevents unexpectedly large backups | Review source scope/storage capacity before raising the limit |
| Source contains a link/device | Backups accept regular files/directories only | Use dedicated data files; do not archive arbitrary system trees |
| Fresh SSH login fails | Key permissions, wrong user/address, or effective policy differs | Use VM console and the [SSH rollback steps](access-and-firewall.md) |

## Controlled service failure

Use a disposable fixture rather than stopping SSH or journald. The automated host integration test includes a `linux-admin-fixture.service`, adds it to the monitored list, stops it, verifies exit 1 and the failure alert, then starts it and verifies recovery.

On your own VM, reproduce this using a temporary service you control. Collect:

```bash
sudo -u linux-admin /usr/local/bin/linux-admin report
sudo journalctl -u linux-admin-alert@linux-admin-monitor.service -n 20 --no-pager
```

Remove your temporary unit from the configured service list when the drill is complete. The project never automatically restarts monitored services.

## Backup and restore drill

1. Add a harmless lab document to the dedicated source with root:linux-admin ownership and 0640 permissions.
2. Start `linux-admin-backup.service` and check its result.
3. Select the new archive and run `verify-backup`.
4. Restore to a new directory in `/var/lib/linux-admin/`.
5. Compare the source and restored contents with `sudo diff -r`.
6. Corrupt a separate copy of the archive and verify that validation fails. Keep the original backup intact.

Run the deterministic unit tests for a full-disk simulation, malicious archive members, overlapping jobs, and retention. These cases use temporary directories and mocks; the real host test separately verifies actual service execution and restore.

## Rotation behavior

The policy rotates daily or when larger than 5 MB at a logrotate invocation. This is not a strict real-time size cap. Fourteen rotated files are retained; the newest rotated file is compressed on a later cycle. The program reopens its log for each event, so rename/create rotation needs no restart or `copytruncate`.

To test rotation:

```bash
sudo logrotate --force /etc/logrotate.d/linux-admin
sudo systemctl start linux-admin-monitor.service
sudo ls -l /var/log/linux-admin/
```

Journald has its own operating-system retention policy. The project does not change that policy.

## Stop or uninstall

Pause schedules without deleting evidence:

```bash
sudo systemctl disable --now linux-admin-monitor.timer linux-admin-backup.timer
sudo systemctl stop linux-admin-monitor.service linux-admin-backup.service
```

To remove the installed code and unit definitions:

```bash
sudo rm /etc/systemd/system/linux-admin-monitor.service /etc/systemd/system/linux-admin-monitor.timer
sudo rm /etc/systemd/system/linux-admin-backup.service /etc/systemd/system/linux-admin-backup.timer
sudo rm /etc/systemd/system/linux-admin-alert@.service
sudo rm /etc/logrotate.d/linux-admin
sudo rm /usr/local/bin/linux-admin /opt/linux-admin/linux_admin.py
sudo systemctl daemon-reload
```

These commands leave configuration, logs, backups, source data, and the service account/group in place for review. Decide separately what data and group memberships to remove. SSH/UFW changes are independent; use their documented rollback procedures.
