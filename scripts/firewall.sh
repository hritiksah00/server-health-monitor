#!/usr/bin/env bash
# Preview by default. Only apply to a fresh lab VM from its console.
set -euo pipefail
mode=preview
confirmed=false
cidr=""
port=22
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
while (($#)); do
    case "$1" in
        --admin-cidr|--ssh-port)
            [[ $# -ge 2 ]] || die "$1 requires a value"
            if [[ "$1" == --admin-cidr ]]; then cidr="$2"; else port="$2"; fi
            shift ;;
        --apply) mode=apply ;;
        --console-confirmed) confirmed=true ;;
        --help)
            printf 'Usage: sudo bash scripts/firewall.sh --admin-cidr ADDRESS/PREFIX [--ssh-port 22] [--apply --console-confirmed]\n'
            exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done
[[ -n "$cidr" ]] || die "--admin-cidr is required"
python3 - "$cidr" "$port" <<'PY'
import ipaddress
import os
import sys
try:
    network = ipaddress.ip_network(sys.argv[1], strict=False)
    port = int(sys.argv[2])
    if network.prefixlen == 0 or not 1 <= port <= 65535:
        raise ValueError("use a restricted admin network and a valid SSH port")
    connection = os.environ.get("SSH_CONNECTION")
    if connection and ipaddress.ip_address(connection.split()[0]) not in network:
        raise ValueError("the current SSH client is outside the proposed admin network")
except ValueError as exc:
    raise SystemExit(f"ERROR: {exc}")
PY
command -v ufw >/dev/null || die "Install ufw before previewing/applying rules"
if [[ "$mode" == apply ]]; then
    [[ "$EUID" -eq 0 ]] || die "--apply requires sudo"
    "$confirmed" || die "--apply requires --console-confirmed on your lab VM"
    current_status="$(LC_ALL=C ufw status)"
    existing_rules="$(LC_ALL=C ufw show added)"
    if grep -q '^Status: active' <<< "$current_status"; then
        die "UFW is already active. Review the existing policy manually."
    fi
    if grep -q '^ufw ' <<< "$existing_rules"; then
        die "UFW already has rules. Review them manually; this script never resets policies."
    fi
fi
run_ufw() {
    if [[ "$mode" == apply ]]; then ufw "$@"; else ufw --dry-run "$@"; fi
}
# Add the management rule before changing defaults or enabling the firewall.
run_ufw allow proto tcp from "$cidr" to any port "$port" comment 'linux-admin management SSH'
run_ufw default deny incoming
run_ufw default allow outgoing
run_ufw logging low
if [[ "$mode" == apply ]]; then
    ufw --force enable
    ufw status verbose
else
    printf '%s\n' 'Preview only: no rules were installed and UFW was not enabled.'
fi
