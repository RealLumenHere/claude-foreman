#!/usr/bin/env bash
# wq#4181 — tf1-b: launch Gatus (external-vantage monitoring). sqlite db
# lives on a container tmpfs mount, never on the eMMC. Run by gatus.service.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
TS_IP="$(tailscale ip -4)"

docker rm -f gatus >/dev/null 2>&1 || true
exec docker run --name gatus --rm \
  --tmpfs /data:rw,size=64m \
  -e GATUS_STORAGE_PATH=/data/gatus.db \
  -v "$SRC/gatus-config.yaml:/config/config.yaml:ro" \
  -p "${TS_IP}:8080:8080" \
  ghcr.io/twin/gatus:v5-arm64
