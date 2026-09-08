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

NOTE (see README.md): LANGFUSE_LAG_ENDPOINT_PATH and LANGFUSE_LAG_FIELD are
still UNVERIFIED against the real v4.32.0 API as of wq#1554 — this worker's
sandbox had no outbound web access and no reach to the Langfuse deploy
config to confirm the containers were even bumped to v4.32.0 yet. Confirm
both against release docs or a live probe before trusting a "no alert"
result as "lag is fine" rather than "wrong endpoint, silently skipping."
EOF
