#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
test -f "$ROOT/runtime/web.env" || { echo "Run scripts/init-config.py first." >&2; exit 1; }
set -a
source "$ROOT/runtime/web.env"
set +a
BINARY="$ROOT/bin/xingzhou-linux-${GOARCH:-amd64}"
test -x "$BINARY" || { echo "Run scripts/build-linux.sh first." >&2; exit 1; }
exec "$BINARY" --host 127.0.0.1 --port "${XINGGUANG_WEB_PORT:-3313}" --open=false --data "$ROOT/runtime/web" --backend-url "http://127.0.0.1:${CHAOXING_SERVICE_PORT:-3100}" --backend-token-file "$ROOT/runtime/backend/api-token" "$@"
