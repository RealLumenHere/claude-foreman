#!/usr/bin/env bash
# wq#1926 — install lumen-kernel-watch. Run as root via `lumen-run --root`
# on lumen itself (repo convention — see ops/wq1652-cfd-actuator,
# ops/wq1373-cf-token-liveness).
#
# Read-only watcher: never upgrades or reboots. It only alerts via
# ntfy-send when Ubuntu 26.04's apt candidate kernel carries the Linux 7.3
# silent-data-loss fix (>= 7.3.0) and the running kernel doesn't.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

echo "== script -> /home/lumen/bin/lumen-kernel-watch =="
install -o lumen -g lumen -m 755 "$SRC/lumen-kernel-watch" /home/lumen/bin/lumen-kernel-watch

echo "== systemd units =="
install -m 644 "$SRC/lumen-kernel-watch.service" /etc/systemd/system/lumen-kernel-watch.service
install -m 644 "$SRC/lumen-kernel-watch.timer" /etc/systemd/system/lumen-kernel-watch.timer
systemctl daemon-reload

echo "== enable =="
systemctl enable --now lumen-kernel-watch.timer
systemctl list-timers lumen-kernel-watch.timer --no-pager || true

echo "== smoke: one pass now rather than waiting for the first timer fire =="
systemctl start lumen-kernel-watch.service
sleep 1
journalctl -u lumen-kernel-watch.service -n 10 --no-pager || true

cat <<EOF

== installed ==
Daily check, +0-30min jitter. Watch it:
  journalctl -u lumen-kernel-watch -f
  python3 /home/lumen/bin/lumen-kernel-watch
EOF
