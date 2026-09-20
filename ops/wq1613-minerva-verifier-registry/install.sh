#!/usr/bin/env bash
# Install lumen-verify (wq#1613): script + registry -> /home/lumen/bin and
# /home/lumen/minerva/verify, units -> systemd. Idempotent.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

python3 -m pytest -q "$HERE/test_lumen_verify.py" "$HERE/test_repair_clobbered_facts.py"

install -m 0755 "$HERE/lumen_verify.py" /home/lumen/bin/lumen-verify
mkdir -p /home/lumen/minerva/verify
install -m 0644 "$HERE/registry.yaml" /home/lumen/minerva/verify/registry.yaml
# wq#2192: after the fixed probes are in place, undo earlier clobbers (idempotent).
python3 "$HERE/repair_clobbered_facts.py" --apply
sudo install -m 0644 "$HERE/lumen-state-verify.service" /etc/systemd/system/lumen-state-verify.service
sudo install -m 0644 "$HERE/lumen-state-verify.timer" /etc/systemd/system/lumen-state-verify.timer
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-state-verify.timer
systemctl is-active --quiet lumen-state-verify.timer
systemctl list-timers lumen-state-verify.timer --no-pager --no-legend

/home/lumen/bin/lumen-verify sweep --dry-run --json
