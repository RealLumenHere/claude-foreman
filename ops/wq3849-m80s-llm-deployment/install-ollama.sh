#!/usr/bin/env bash
# wq#3849 — install Ollama on M80s, pin to P-cores, apply fleet env tuning.
#
# Run on M80s itself, in a privileged/root session, after wq#3703 (Ubuntu install)
# and wq#3705 (fworker user/NVMe) have landed. This script does not touch any other
# host and does not pull models (see pull-models.sh — that step is gated separately,
# wq#1297).
#
# Idempotent: safe to re-run. Each step checks before acting.
set -euo pipefail

DROPIN_DIR=/etc/systemd/system/ollama.service.d
DROPIN_SRC="$(dirname "${BASH_SOURCE[0]}")/ollama-pcore-pin.conf"

if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "ollama already installed: $(ollama --version)"
fi

mkdir -p "$DROPIN_DIR"
install -m 0644 "$DROPIN_SRC" "$DROPIN_DIR/10-pcore-pin.conf"

systemctl daemon-reload
systemctl enable --now ollama
systemctl restart ollama

sleep 2
curl -fsS --max-time 5 http://127.0.0.1:11434/api/version
echo
echo "INSTALL_OK — verify CPU pin with: systemctl show ollama -p CPUAffinity"
