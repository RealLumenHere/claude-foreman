#!/usr/bin/env bash
# wq#3212 — deploy the shared rung-list module + updated monitors.
# Run as root via lumen-run --root on lumen (ops/wq900 convention). Idempotent.
#
# The litellm_config.yaml edit itself is NOT here: remove_dead_rungs.py runs in
# the Mon 2026-10-05 wq#1402 window right before `litellm-upgrade --apply`, so
# the removal rides that one recreate (see README.md "Window plan").
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
BIN=/home/lumen/bin
WD=/home/lumen/agents/model-watchdog/lumen_model_watchdog.py

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/litellm_rungs.py" "$BIN/litellm_rungs.py"
  vfile "$SRC/../wq3169-litellm-completion-probe/litellm-completion-probe" "$BIN/litellm-completion-probe"
  vfile "$SRC/../wq2903-model-ref-drift/lumen_model_watchdog.py" "$WD"
  vactive litellm-completion-probe.timer
  # post-window only: the dead rungs are gone from the gateway config
  for rung in claude-opus-5 claude-max-sonnet claude-max-opus lumen-lfm2.5-2.6b kimik3-0.4b-local; do
    if grep -q "model_name: *['\"]*$rung" /home/lumen/agent/litellm_config.yaml 2>/dev/null; then
      _vmiss "dead rung still in litellm_config.yaml: $rung (window op not yet run)"
    fi
  done
  vdone
fi

[ "$(id -u)" -eq 0 ] || { echo "run as root (lumen-run --root)"; exit 1; }
INSTALL="$SRC/../wq2856-atomic-install/install-script"

echo "== shared rung-list module -> $BIN =="
"$INSTALL" -m 0644 "$SRC/litellm_rungs.py" "$BIN/litellm_rungs.py"
chown lumen:lumen "$BIN/litellm_rungs.py"

echo "== completion probe (reads shared module, aq auto-clear) =="
"$INSTALL" -m 0755 "$SRC/../wq3169-litellm-completion-probe/litellm-completion-probe" "$BIN/litellm-completion-probe"
chown lumen:lumen "$BIN/litellm-completion-probe"

echo "== model-watchdog (config-derived roster union) =="
cp "$WD" "$WD.bak.$(date +%Y%m%d_%H%M%S)_pre_wq3212"
"$INSTALL" "$SRC/../wq2903-model-ref-drift/lumen_model_watchdog.py" "$WD"
chown lumen:lumen "$WD"

echo "== first runs =="
systemctl start lumen-model-watchdog.service || true
journalctl -u lumen-model-watchdog.service -n 3 --no-pager -o cat || true
systemctl start litellm-completion-probe.service || true

"$0" --verify
