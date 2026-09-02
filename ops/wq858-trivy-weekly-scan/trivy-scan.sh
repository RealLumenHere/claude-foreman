#!/usr/bin/env bash
# wq#858 — weekly Trivy scan: every running container image + the Lumen
# filesystem itself. Runs as root (trivy-scan.service) for docker.sock access
# and unrestricted filesystem read; DB refresh is the only step that touches
# the network, everything after is offline. See README.md for the deploy path
# — this script only runs where it's installed (root, via install.sh).
set -euo pipefail

TRIVY_BIN=${TRIVY_BIN:-/usr/local/bin/trivy}
REPORT_DIR=${TRIVY_REPORT_DIR:-/home/lumen/maint/trivy}
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"

SCAN_DIR=$(mktemp -d)
trap 'rm -rf "$SCAN_DIR"' EXIT

echo "== refreshing offline vulnerability DB =="
"$TRIVY_BIN" image --download-db-only

echo "== scanning running container images =="
i=0
while IFS= read -r image; do
  [ -n "$image" ] || continue
  i=$((i + 1))
  safe=$(printf '%s' "$image" | tr -c 'a-zA-Z0-9_.-' '_')
  "$TRIVY_BIN" image --skip-db-update --format json \
    --output "${SCAN_DIR}/image_${i}_${safe}.json" "$image"
done < <(docker ps --format '{{.Image}}' | sort -u)

echo "== scanning Lumen filesystem =="
"$TRIVY_BIN" rootfs --skip-db-update --format json \
  --skip-dirs /proc --skip-dirs /sys --skip-dirs /dev --skip-dirs /run \
  --skip-dirs /home/lumen/worktrees --skip-dirs /var/lib/docker \
  --output "${SCAN_DIR}/rootfs.json" /

echo "== generating report + ntfy digest =="
python3 "${SELF_DIR}/trivy_report.py" --scan-dir "$SCAN_DIR" --report-dir "$REPORT_DIR"
