#!/usr/bin/env bash
# Start/stop the migrated local ERPNext 17 site without rebuilding images.
set -euo pipefail
infra="$(cd "$(dirname "${BASH_SOURCE[0]}")/../infra" && pwd)"
env_file=/var/lib/erpnext/v17-live/live.env
test -f "$env_file" || { echo 'The ERPNext 17 site has not been initialized.' >&2; exit 1; }
exec docker compose --env-file "$env_file" -f "$infra/live.compose.yaml" "$@"
