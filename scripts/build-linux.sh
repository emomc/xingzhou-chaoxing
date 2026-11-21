#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
mkdir -p "$ROOT/bin"
cd "$ROOT/server-web"
GOOS=linux GOARCH="${GOARCH:-amd64}" CGO_ENABLED=0 go build -trimpath -o "$ROOT/bin/xingzhou-linux-${GOARCH:-amd64}" ./cmd/xingguang
echo "Linux server binary built in bin/."
