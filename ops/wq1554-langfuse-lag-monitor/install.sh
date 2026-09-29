#!/usr/bin/env bash
# wq#1554 — install langfuse-lag-monitor. Run as root via `lumen-run --root`
# (repo convention: ops/wq900-primary-tree-guard, ops/wq1184-gatus-health-checks).
#
# The service executes harness/langfuse_lag_monitor.py from the
# /home/lumen/foreman PRIMARY tree, same convention as
# ops/wq900-primary-tree-guard/install.sh: the script is already a harness
# module (imports langfuse_client from the same directory via Python's
# implicit script-dir sys.path entry), so there is no separate deploy copy to
# keep in sync — whatever is on foreman's main branch is what runs.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
# The service runs harness/langfuse_lag_monitor.py straight from the foreman
# primary tree, so there is no deploy copy to compare -- only the units are ours.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vunit_file "$SRC/langfuse-lag-monitor.service" \
             /etc/systemd/system/langfuse-lag-monitor.service
  vunit_file "$SRC/langfuse-lag-monitor.timer" \
             /etc/systemd/system/langfuse-lag-monitor.timer
  venabled   langfuse-lag-monitor.timer
  vactive    langfuse-lag-monitor.timer
  vdone
fi

echo "== systemd units =="
install -m 644 "$SRC/langfuse-lag-monitor.service" /etc/systemd/system/langfuse-lag-monitor.service
install -m 644 "$SRC/langfuse-lag-monitor.timer" /etc/systemd/system/langfuse-lag-monitor.timer
systemctl daemon-reload

echo "== enable =="
systemctl enable --now langfuse-lag-monitor.timer
systemctl list-timers langfuse-lag-monitor.timer --no-pager || true

cat <<EOF

== installed ==
Next pass runs within 5 minutes, then every 15 minutes. Watch it:
  journalctl -u langfuse-lag-monitor -f
  python3 /home/lumen/foreman/harness/langfuse_lag_monitor.py

NOTE (see README.md): as of wq#3124 the probe is canary read-back
(POST /api/public/ingestion, then GET /api/public/traces/{id}), verified
against the live v4.32.0 instance on 2026-09-29 — the wq#1552 export-lag
endpoint never existed and its LANGFUSE_LAG_ENDPOINT_PATH/LANGFUSE_LAG_FIELD
knobs are gone. State lives at LANGFUSE_LAG_STATE (default
/home/lumen/.local/state/langfuse_lag_monitor.json); threshold is
LANGFUSE_LAG_THRESHOLD_S (default 2700s = 3 poll intervals).
EOF
