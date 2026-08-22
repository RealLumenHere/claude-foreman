#!/usr/bin/env bash
# wq#763 A13 — deploy the queue flow-metrics textfile exporter onto node_exporter (wq#753's
# textfile collector, already live on this host). Unprivileged, systemd --user, idempotent:
# safe to rerun after any change to opsaudit_a13_flow_metrics.py.
#
# Requires: XDG_RUNTIME_DIR pointed at the running user session bus, e.g.:
#   XDG_RUNTIME_DIR=/run/user/1000 ./opsaudit_a13_deploy_flow_exporter.sh
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/node-exporter/bin"
TEXTFILE_DIR="$HOME/.local/node-exporter/textfile"
UNIT_DIR="$HOME/.config/systemd/user"

mkdir -p "$BIN_DIR" "$TEXTFILE_DIR" "$UNIT_DIR"

cp "$SRC_DIR/opsaudit_a13_flow_metrics.py" "$BIN_DIR/opsaudit_a13_flow_metrics.py.new"
mv "$BIN_DIR/opsaudit_a13_flow_metrics.py.new" "$BIN_DIR/opsaudit_a13_flow_metrics.py"

cat > "$BIN_DIR/opsaudit_a13_flow_textfile.sh" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
OUT="$HOME/.local/node-exporter/textfile/queue_flow.prom"
python3 "$HOME/.local/node-exporter/bin/opsaudit_a13_flow_metrics.py" --prom > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
SCRIPT
chmod +x "$BIN_DIR/opsaudit_a13_flow_textfile.sh"

cat > "$UNIT_DIR/opsaudit-a13-flow-metrics.service" <<'UNIT'
[Unit]
Description=Sample queue.db flow metrics into node_exporter textfile collector (wq#763)

[Service]
Type=oneshot
ExecStart=%h/.local/node-exporter/bin/opsaudit_a13_flow_textfile.sh
UNIT

cat > "$UNIT_DIR/opsaudit-a13-flow-metrics.timer" <<'UNIT'
[Unit]
Description=Sample queue.db flow metrics every 60s (wq#763)

[Timer]
OnBootSec=15s
OnUnitActiveSec=60s

[Install]
WantedBy=timers.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now opsaudit-a13-flow-metrics.timer
systemctl --user start opsaudit-a13-flow-metrics.service

echo "[deployed] textfile: $TEXTFILE_DIR/queue_flow.prom"
echo "[deployed] timer status:"
systemctl --user is-active opsaudit-a13-flow-metrics.timer
