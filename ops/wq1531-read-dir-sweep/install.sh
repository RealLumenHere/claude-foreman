#!/usr/bin/env bash
# wq#1531 — install lumen-read-dir-sweep.{service,timer}. Run as root via
# lumen-run on the target host itself (this repo's convention — see
# ops/wq859-renovate-gitea/install.sh, ops/wq1397-gitea-version-check/install.sh):
#
#   /home/lumen/bin/lumen-run start --root -- bash \
#       /home/lumen/foreman/ops/wq1531-read-dir-sweep/install.sh
#
# WHY this exists at all: PR#357 (wq#1482) landed the units under
# harness/systemd/ and stopped there, so capability_lint's fresh-hit gate had no
# sweep to read and every dir(X) derivation fleet-wide fell to `unresolved`.
# Same shape as wq#1404. Idempotent: safe to re-run.
set -euo pipefail

SRC=/home/lumen/foreman/harness/systemd
RUNTIME=/home/lumen/foreman/harness/runtime/read_dir_sweep.json

install -D -m 644 "$SRC/lumen-read-dir-sweep.service" /etc/systemd/system/lumen-read-dir-sweep.service
install -D -m 644 "$SRC/lumen-read-dir-sweep.timer"   /etc/systemd/system/lumen-read-dir-sweep.timer
systemctl daemon-reload
systemctl enable --now lumen-read-dir-sweep.timer

# Seed the runtime file now rather than waiting for the first 05:15 OnCalendar
# fire: until it exists, sweep_stale(None) is True and the gate fails closed on
# every dir tag. Starting the unit (not python3 directly) seeds it as User=lumen
# and proves the installed unit itself works.
systemctl start lumen-read-dir-sweep.service
systemctl is-active --quiet lumen-read-dir-sweep.timer

echo "lumen-read-dir-sweep.timer installed and enabled (OnCalendar 05:15, Persistent)"
ls -l "$RUNTIME"
systemctl list-timers --no-pager lumen-read-dir-sweep.timer
