#!/usr/bin/env bash
# wq#3122 — install langfuse-trace-rate-monitor. Run as root via `lumen-run --root`
# (repo convention: ops/wq1554-langfuse-lag-monitor, ops/wq900-primary-tree-guard).
#
# The service executes harness/langfuse_trace_rate_monitor.py from the
# /home/lumen/foreman PRIMARY tree, same convention as
# ops/wq1554-langfuse-lag-monitor/install.sh: the script is already a harness
# module (imports langfuse_client from the same directory via Python's
# implicit script-dir sys.path entry), so there is no separate deploy copy to
# keep in sync — whatever is on foreman's main branch is what runs.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vunit_file "$SRC/langfuse-trace-rate-monitor.service" \
             /etc/systemd/system/langfuse-trace-rate-monitor.service
  vunit_file "$SRC/langfuse-trace-rate-monitor.timer" \
             /etc/systemd/system/langfuse-trace-rate-monitor.timer
  venabled   langfuse-trace-rate-monitor.timer
  vactive    langfuse-trace-rate-monitor.timer
  vdone
fi

echo "== systemd units =="
install -m 644 "$SRC/langfuse-trace-rate-monitor.service" /etc/systemd/system/langfuse-trace-rate-monitor.service
install -m 644 "$SRC/langfuse-trace-rate-monitor.timer" /etc/systemd/system/langfuse-trace-rate-monitor.timer
systemctl daemon-reload

echo "== enable =="
systemctl enable --now langfuse-trace-rate-monitor.timer
systemctl list-timers langfuse-trace-rate-monitor.timer --no-pager || true

cat <<EOF

== installed ==
Next pass runs within 5 minutes, then every 15 minutes. Watch it:
  journalctl -u langfuse-trace-rate-monitor -f
  python3 /home/lumen/foreman/harness/langfuse_trace_rate_monitor.py
EOF
