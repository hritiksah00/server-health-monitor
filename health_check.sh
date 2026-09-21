#!/usr/bin/env bash
# Compatibility entry point for the original monitor; local mode needs no sudo.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
config="$SCRIPT_DIR/config/local.json"
if [[ -v LINUX_ADMIN_CONFIG ]]; then config="$LINUX_ADMIN_CONFIG"; fi
exec python3 "$SCRIPT_DIR/linux_admin.py" --config "$config" monitor "$@"
