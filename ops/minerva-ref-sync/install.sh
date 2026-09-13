#!/usr/bin/env bash
# Install the sweep-gated minerva-ref mirror sync (foreman ops/minerva-ref-sync).
# Runs the acceptance tests first: a mirror that would carry a secret must refuse to publish.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
python3 -m pytest -q "$HERE/test_minerva_ref_sync.py"
install -m 0755 "$HERE/minerva_ref_sync.py" /home/lumen/bin/minerva-ref-sync
# First publish (or refusal) before the timer exists, so a bad tree is caught interactively.
/home/lumen/bin/minerva-ref-sync --json
sudo install -m 0644 "$HERE/lumen-minerva-ref-sync.service" /etc/systemd/system/lumen-minerva-ref-sync.service
sudo install -m 0644 "$HERE/lumen-minerva-ref-sync.timer" /etc/systemd/system/lumen-minerva-ref-sync.timer
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-minerva-ref-sync.timer
systemctl is-active --quiet lumen-minerva-ref-sync.timer
systemctl list-timers lumen-minerva-ref-sync.timer --no-pager --no-legend
python3 -c "import json;m=json.load(open('/home/lumen/research/minerva-ref/.minerva-ref-sync.json'));print('mirror:',m['status'],m['files'],'files',m['hits'],'hits',m['ts'])"
