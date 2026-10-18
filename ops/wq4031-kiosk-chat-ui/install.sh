#!/usr/bin/env bash
# wq#4031 — Lumen chat kiosk web UI (FastAPI server.py + static/ + vad.py).
# Run as root, e.g.:
#   /home/lumen/bin/lumen-run start --root -- bash /home/lumen/foreman/ops/wq4031-kiosk-chat-ui/install.sh
# (this repo's convention for privileged unit installs — see ops/wq2092-cert-watch).
#
# Deploy host: Lumen itself (port 8099 -- 8080 is already ntfy's lumen-updates
# listener). This only stands the server up and serves text chat + camera
# snapshot (ML-free); wq#4030 (RK3399 Face X8 WebView sideload, lane frank-hw,
# open) still has to point the on-device WebView at this host:port, and voice/
# TTS still need the separate voice-venv (see README.md).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/services/kiosk-chat-ui

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/server.py" "$DEST/server.py"
  vfile "$SRC/vad.py" "$DEST/vad.py"
  vfile "$SRC/static/index.html" "$DEST/static/index.html"
  vunit_file "$SRC/kiosk-chat-ui.service" /etc/systemd/system/kiosk-chat-ui.service
  vactive kiosk-chat-ui.service
  venabled kiosk-chat-ui.service
  vdone
fi

mkdir -p "$DEST/static"
install -o lumen -g lumen -m 755 "$SRC/server.py" "$DEST/server.py"
install -o lumen -g lumen -m 755 "$SRC/vad.py" "$DEST/vad.py"
install -o lumen -g lumen -m 644 "$SRC/static/index.html" "$SRC/static/app.js" "$SRC/static/style.css" "$DEST/static/"

cp "$SRC/kiosk-chat-ui.service" /etc/systemd/system/kiosk-chat-ui.service
systemctl daemon-reload
systemctl enable --now kiosk-chat-ui.service

echo "kiosk-chat-ui.service installed and running on :8099 (text chat + camera snapshot; voice/tts need the voice-venv)"
echo "logs:     journalctl -u kiosk-chat-ui.service -n 100 --no-pager"
echo "rollback: systemctl disable --now kiosk-chat-ui.service; rm /etc/systemd/system/kiosk-chat-ui.service; rm -r $DEST"
