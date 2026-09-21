#!/usr/bin/env bash
# Installs only this lab's files/accounts/timers. No SSH or firewall changes.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
PROJECT_DIR="$(cd -- "$(dirname -- "$0")/.." && pwd)"
apply=false
operator=""
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
while (($#)); do
    case "$1" in
        --apply) apply=true ;;
        --operator)
            [[ $# -ge 2 ]] || die "--operator needs an existing username"
            operator="$2"
            shift ;;
        --help)
            printf 'Usage: sudo bash scripts/install.sh --apply [--operator EXISTING_USER]\n'
            exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done
if ! "$apply"; then
    printf '%s\n' 'Plan: install code/config, create linux-admin service user/group, set directory permissions, install logrotate and systemd units, create first backup, enable timers.'
    printf '%s\n' 'SSH and UFW are separate opt-in steps. Run with --apply on a disposable Ubuntu VM.'
    exit 0
fi
[[ "$EUID" -eq 0 ]] || die "Installation requires sudo"
[[ -d /run/systemd/system ]] || die "A Linux host booted with systemd is required"
for command in python3 systemctl systemd-analyze logrotate groupadd useradd usermod getent install; do
    command -v "$command" >/dev/null || die "Missing dependency: $command"
done
if [[ -n "$operator" ]]; then
    [[ "$operator" =~ ^[a-z_][a-z0-9_-]*$ ]] || die "Invalid operator username"
    id "$operator" >/dev/null || die "Operator must already exist"
    [[ "$(id -u "$operator")" != 0 ]] || die "Choose a non-root operator"
fi
for directory in /opt/linux-admin /etc/linux-admin /var/log/linux-admin /var/lib/linux-admin /var/backups/linux-admin /srv/linux-admin /srv/linux-admin/data; do
    [[ ! -L "$directory" ]] || die "Refusing a symlink installation directory: $directory"
done
if [[ -e /usr/local/bin/linux-admin || -L /usr/local/bin/linux-admin ]]; then
    [[ "$(readlink /usr/local/bin/linux-admin)" == /opt/linux-admin/linux_admin.py ]] || die "linux-admin command already belongs to another installation"
fi
getent group linux-admin >/dev/null || groupadd --system linux-admin
if getent passwd linux-admin >/dev/null; then
    [[ "$(id -gn linux-admin)" == linux-admin ]] || die "Existing account has an unexpected primary group"
    [[ "$(getent passwd linux-admin | cut -d: -f6)" == /var/lib/linux-admin ]] || die "Existing account has an unexpected home"
    [[ "$(getent passwd linux-admin | cut -d: -f7)" == /usr/sbin/nologin ]] || die "Existing account is not a dedicated service account"
else
    useradd --system --no-create-home --gid linux-admin --home-dir /var/lib/linux-admin --shell /usr/sbin/nologin linux-admin
fi
install -d -o root -g root -m 0755 /opt/linux-admin
install -o root -g root -m 0755 "$PROJECT_DIR/linux_admin.py" /opt/linux-admin/linux_admin.py
ln -sfn /opt/linux-admin/linux_admin.py /usr/local/bin/linux-admin
install -d -o root -g linux-admin -m 0750 /etc/linux-admin
[[ ! -L /etc/linux-admin/config.json ]] || die "Configuration may not be a symlink"
if [[ ! -e /etc/linux-admin/config.json ]]; then
    install -o root -g linux-admin -m 0640 "$PROJECT_DIR/config/server.json" /etc/linux-admin/config.json
fi
for directory in /var/log/linux-admin /var/lib/linux-admin /var/backups/linux-admin; do
    install -d -o linux-admin -g linux-admin -m 0750 "$directory"
done
install -d -o root -g linux-admin -m 0750 /srv/linux-admin
install -d -o root -g linux-admin -m 2750 /srv/linux-admin/data
if [[ ! -e /srv/linux-admin/data/service-notes.txt ]]; then
    install -o root -g linux-admin -m 0640 "$PROJECT_DIR/examples/data/service-notes.txt" /srv/linux-admin/data/service-notes.txt
fi
/usr/bin/python3 /opt/linux-admin/linux_admin.py validate-config
if [[ -n "$operator" ]]; then
    usermod --append --groups linux-admin "$operator"
    printf 'Operator %s can read lab logs/backups after a fresh login; no sudo access is granted.\n' "$operator"
fi
install -o root -g root -m 0644 "$PROJECT_DIR/config/logrotate/linux-admin" /etc/logrotate.d/linux-admin
for unit in "$PROJECT_DIR"/systemd/*; do
    install -o root -g root -m 0644 "$unit" /etc/systemd/system/
done
systemd-analyze verify /etc/systemd/system/linux-admin-*.service /etc/systemd/system/linux-admin-*.timer
logrotate --debug /etc/logrotate.d/linux-admin
systemctl daemon-reload
systemctl start linux-admin-backup.service
systemctl enable --now linux-admin-monitor.timer linux-admin-backup.timer
if ! systemctl start linux-admin-monitor.service; then
    printf '%s\n' 'Monitor reported a warning/error. Inspect: sudo journalctl -u linux-admin-monitor.service -n 30'
fi
printf '%s\n' 'Installed. Inspect timers: systemctl list-timers "linux-admin-*" --all'
