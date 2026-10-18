#!/usr/bin/env bash
# wq#4031 — Lumen chat kiosk web UI (FastAPI server.py + static/ + vad.py).
#
# No deploy target exists yet. This server has to be running somewhere
# reachable before wq#4030 (RK3399 Face X8: sideload the Webview Kiosk APK,
# lane frank-hw, open, detail: "Requires: Lumen chat web server deployed
# first") can point the on-device WebView at it. The host and systemd unit
# name for that are undecided (see README.md "Running" — today this is run
# by hand with `uvicorn server:app`), so there is no install step to write
# yet. Per ops/VERIFY_CONTRACT.md, a bundle with no install step still needs
# a --verify handler — this one can only assert the honest negative: nothing
# runs this anywhere yet.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if ! systemctl list-unit-files 2>/dev/null | grep -qi 'kiosk-chat'; then
    _vmiss "not deployed: no kiosk-chat-ui systemd unit on Lumen (deploy target undecided -- wq#4030, frank-hw, open, needs the chat web server deployed first)"
  fi
  vdone
fi

echo "wq#4031: no install step defined yet -- deploy host/unit undecided (see README.md, wq#4030)." >&2
exit 1
