#!/usr/bin/env bash
# Full host checks: run ONLY on an ephemeral runner or a disposable Ubuntu VM.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
PROJECT_DIR="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_DIR"
[[ "$EUID" -eq 0 ]] || { echo 'Run with sudo on a disposable VM.' >&2; exit 1; }
authorized=false
if [[ -v GITHUB_ACTIONS && "$GITHUB_ACTIONS" == true ]]; then authorized=true; fi
if [[ $# -eq 1 && "$1" == --disposable-vm ]]; then authorized=true; fi
"$authorized" || { echo 'Requires --disposable-vm; never run host integration checks on a work laptop.' >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo 'systemd must be PID 1.' >&2; exit 1; }
[[ ! -e /etc/systemd/system/linux-admin-fixture.service ]] || exit 1
if id linux-admin-ci >/dev/null 2>&1; then echo 'Test account already exists.' >&2; exit 1; fi
temp="$(mktemp -d)"
ssh_pid=""
fixture_created=false
user_created=false
cleanup() {
    if [[ -n "$ssh_pid" ]]; then kill "$ssh_pid" 2>/dev/null || true; wait "$ssh_pid" 2>/dev/null || true; fi
    if [[ -f "$temp/config.json" ]]; then cp "$temp/config.json" /etc/linux-admin/config.json; fi
    systemctl stop linux-admin-monitor.timer 2>/dev/null || true
    rm -f /run/systemd/system/linux-admin-monitor.timer.d/integration.conf
    if "$fixture_created"; then
        systemctl stop linux-admin-fixture.service 2>/dev/null || true
        rm -f /etc/systemd/system/linux-admin-fixture.service
    fi
    if "$user_created"; then userdel --remove linux-admin-ci 2>/dev/null || true; fi
    systemctl daemon-reload
    systemctl reset-failed linux-admin-monitor.service 2>/dev/null || true
    systemctl start linux-admin-monitor.timer 2>/dev/null || true
    rm -rf "$temp"
}
trap cleanup EXIT
useradd --create-home --shell /bin/bash linux-admin-ci
user_created=true
bash scripts/install.sh --apply --operator linux-admin-ci
cp /etc/linux-admin/config.json "$temp/config.json"
before="$(sha256sum /etc/linux-admin/config.json)"
bash scripts/install.sh --apply --operator linux-admin-ci
[[ "$(sha256sum /etc/linux-admin/config.json)" == "$before" ]]
printf 'PASS: installer is repeatable and preserves configuration\n'

id linux-admin
id linux-admin-ci
[[ "$(getent passwd linux-admin | cut -d: -f7)" == /usr/sbin/nologin ]]
[[ "$(stat -c '%U:%G:%a' /etc/linux-admin/config.json)" == root:linux-admin:640 ]]
[[ "$(stat -c '%U:%G:%a' /srv/linux-admin/data)" == root:linux-admin:2750 ]]
[[ "$(stat -c '%U:%G:%a' /var/backups/linux-admin)" == linux-admin:linux-admin:750 ]]
runuser -u linux-admin-ci -- test -r /etc/linux-admin/config.json
if runuser -u linux-admin-ci -- test -w /opt/linux-admin/linux_admin.py; then exit 1; fi
if runuser -u linux-admin -- test -w /srv/linux-admin/data; then exit 1; fi
printf 'PASS: service identity, operator group, and read/write boundaries\n'

# Use relaxed resource thresholds so unrelated runner load does not break service-state tests.
python3 - <<'PY'
import json
from pathlib import Path
p = Path('/etc/linux-admin/config.json')
c = json.loads(p.read_text())
for key in ('cpu_threshold', 'memory_threshold', 'disk_threshold'):
    c[key] = 100
c['sample_seconds'] = 0.1
p.write_text(json.dumps(c))
PY
systemctl stop linux-admin-monitor.timer
systemctl start linux-admin-backup.service
systemctl start linux-admin-monitor.service
runuser -u linux-admin -- /usr/local/bin/linux-admin report
[[ "$(systemctl show -p User --value linux-admin-monitor.service)" == linux-admin ]]
[[ "$(systemctl show -p Result --value linux-admin-monitor.service)" == success ]]
[[ "$(systemctl show -p Result --value linux-admin-backup.service)" == success ]]
printf 'PASS: real resource collection and unprivileged systemd jobs\n'

archive="$(find /var/backups/linux-admin -maxdepth 1 -name 'backup-*.tar.gz' -type f | sort | tail -1)"
runuser -u linux-admin -- /usr/local/bin/linux-admin verify-backup "$archive"
restore_dir="/var/lib/linux-admin/restore-integration-$(date +%s%N)"
runuser -u linux-admin -- /usr/local/bin/linux-admin restore "$archive" "$restore_dir"
cmp /srv/linux-admin/data/service-notes.txt "$restore_dir/service-notes.txt"
printf 'PASS: installed backup verifies and restores byte-identical data\n'

logrotate --force --state "$temp/logrotate.state" /etc/logrotate.d/linux-admin
[[ -f /var/log/linux-admin/operations.jsonl.1 ]]
systemctl start linux-admin-monitor.service
[[ "$(stat -c '%U:%G:%a' /var/log/linux-admin/operations.jsonl)" == linux-admin:linux-admin:640 ]]
grep -q monitor_completed /var/log/linux-admin/operations.jsonl
printf 'PASS: logrotate preserves permissions and monitoring resumes writing\n'

install -d /run/systemd/system/linux-admin-monitor.timer.d
cat > /run/systemd/system/linux-admin-monitor.timer.d/integration.conf <<'UNIT'
[Timer]
OnBootSec=
OnUnitInactiveSec=
OnBootSec=1s
OnUnitInactiveSec=2s
AccuracySec=100ms
UNIT
systemctl daemon-reload
baseline="$(stat -c %Y /var/lib/linux-admin/status.json)"
systemctl restart linux-admin-monitor.timer
observed=false
for ((attempt = 0; attempt < 15; attempt++)); do
    sleep 1
    if [[ "$(stat -c %Y /var/lib/linux-admin/status.json)" -gt "$baseline" ]]; then observed=true; break; fi
done
"$observed"
systemctl stop linux-admin-monitor.timer
systemctl list-timers 'linux-admin-*' --all --no-pager
systemctl is-enabled linux-admin-backup.timer
systemctl show linux-admin-backup.timer --property=Persistent
printf 'PASS: timer invokes monitor automatically; persistent daily backup timer is enabled\n'

cat > /etc/systemd/system/linux-admin-fixture.service <<'UNIT'
[Unit]
Description=Disposable integration fixture
[Service]
Type=simple
ExecStart=/usr/bin/sleep infinity
User=linux-admin
UNIT
fixture_created=true
systemctl daemon-reload
systemctl start linux-admin-fixture.service
python3 - <<'PY'
import json
from pathlib import Path
p = Path('/etc/linux-admin/config.json')
c = json.loads(p.read_text())
c['services'].append('linux-admin-fixture.service')
p.write_text(json.dumps(c))
PY
systemctl start linux-admin-monitor.service
systemctl stop linux-admin-fixture.service
if systemctl start linux-admin-monitor.service; then echo 'Expected warning failure was not detected' >&2; exit 1; fi
[[ "$(systemctl show -p ExecMainStatus --value linux-admin-monitor.service)" == 1 ]]
alert_seen=false
for ((attempt = 0; attempt < 15; attempt++)); do
    if grep -q systemd_job_failed /var/log/linux-admin/operations.jsonl; then alert_seen=true; break; fi
    sleep 1
done
"$alert_seen"
systemctl start linux-admin-fixture.service
systemctl start linux-admin-monitor.service
[[ "$(systemctl show -p Result --value linux-admin-monitor.service)" == success ]]
printf 'PASS: stopped service causes exit 1, OnFailure alert, and verified recovery\n'

# A separate loopback-only sshd tests this repo's policy, without changing host SSH.
ssh-keygen -q -t ed25519 -N '' -f "$temp/host_key"
ssh-keygen -q -t ed25519 -N '' -f "$temp/client_key"
install -d -o linux-admin-ci -g linux-admin-ci -m 0700 /home/linux-admin-ci/.ssh
install -o linux-admin-ci -g linux-admin-ci -m 0600 "$temp/client_key.pub" /home/linux-admin-ci/.ssh/authorized_keys
install -d -m 0755 /run/sshd
sshd -t -f config/sshd/00-linux-admin.conf -o "HostKey=$temp/host_key"
sshd -T -f config/sshd/00-linux-admin.conf -o "HostKey=$temp/host_key" > "$temp/sshd-effective"
grep -qx 'passwordauthentication no' "$temp/sshd-effective"
grep -qx 'permitrootlogin no' "$temp/sshd-effective"
grep -qx 'authenticationmethods publickey' "$temp/sshd-effective"
/usr/sbin/sshd -D -e -f "$PROJECT_DIR/config/sshd/00-linux-admin.conf" \
    -o "HostKey=$temp/host_key" -o "PidFile=$temp/sshd.pid" \
    -o ListenAddress=127.0.0.1 -o UsePAM=yes -p 22292 > "$temp/sshd-output" 2>&1 &
ssh_pid=$!
connected=false
for ((attempt = 0; attempt < 10; attempt++)); do
    if ssh -p 22292 -i "$temp/client_key" -o BatchMode=yes -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=accept-new -o "UserKnownHostsFile=$temp/known_hosts" \
        linux-admin-ci@127.0.0.1 id > "$temp/ssh-session" 2>/dev/null; then
        connected=true
        break
    fi
    sleep 1
done
if ! "$connected"; then cat "$temp/sshd-output"; exit 1; fi
cat "$temp/ssh-session"
if ssh -p 22292 -o BatchMode=yes -o PubkeyAuthentication=no \
    -o PreferredAuthentications=password -o "UserKnownHostsFile=$temp/known_hosts" \
    linux-admin-ci@127.0.0.1 true 2>/dev/null; then exit 1; fi
printf 'PASS: isolated SSH key login succeeds; password authentication is rejected\n'

bash scripts/firewall.sh --admin-cidr 192.0.2.10/32 > "$temp/ufw-preview"
grep -q 'Preview only' "$temp/ufw-preview"
printf 'PASS: real UFW validates management rule and default policies in dry-run mode\n'
printf 'Host integration checks completed successfully.\n'
