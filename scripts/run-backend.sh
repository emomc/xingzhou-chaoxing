#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
test -f "$ROOT/runtime/backend/api-token" || { echo "Run scripts/init-config.py first." >&2; exit 1; }
cd "$ROOT/chaoxing-3.1.4"
exec "$ROOT/.venv/bin/python" -m backend --host 127.0.0.1 --port "${CHAOXING_SERVICE_PORT:-3100}" --state-dir "$ROOT/runtime/backend" "$@"
