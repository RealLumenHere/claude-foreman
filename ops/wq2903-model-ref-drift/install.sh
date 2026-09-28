#!/usr/bin/env bash
# wq#2903 — callers of the retired mistral-small rung + the lint that stops the
# class recurring. Run as root via lumen-run --root on lumen (ops/wq900 convention).
# Idempotent: safe to re-run. Rollback: README.md.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
BIN=/home/lumen/bin
WD=/home/lumen/agents/model-watchdog/lumen_model_watchdog.py
QM=/home/lumen/agent/lumen_quota_manager.py

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/lumen_model_watchdog.py" "$WD"
  vfile "$SRC/model_ref_lint.py" "$BIN/model-ref-lint"
  vunit_file "$SRC/lumen-model-ref-lint.service" /etc/systemd/system/lumen-model-ref-lint.service
  vunit_file "$SRC/lumen-model-ref-lint.timer" /etc/systemd/system/lumen-model-ref-lint.timer
  venabled lumen-model-ref-lint.timer
  vactive lumen-model-ref-lint.timer
  vcontains "$QM" '"mistral": +"deepseek-v4-flash"'
  vcontains /home/lumen/minerva/model_audit.py '"deepseek-v4-pro"\)  # wq#2903'
  # the fleet worker imports PROVIDER_TO_MODEL once: it must have restarted after the patch
  started="$(date -d "$(systemctl show -p ActiveEnterTimestamp --value lumen-fleet-mistral.service)" +%s 2>/dev/null || echo 0)"
  [ "$started" -ge "$(stat -c %Y "$QM")" ] || _vmiss "lumen-fleet-mistral.service not restarted since $QM was patched"
  vdone
fi

[ "$(id -u)" -eq 0 ] || { echo "run as root (lumen-run --root)"; exit 1; }
INSTALL="$SRC/../wq2856-atomic-install/install-script"

echo "== callers: mistral-small -> live rungs =="
python3 "$SRC/patch_callers.py"
chown lumen:lumen "$QM" /home/lumen/minerva/model_audit.py

echo "== model-watchdog: live /v1/models roster gate =="
cp "$WD" "$WD.bak.$(date +%Y%m%d_%H%M%S)_pre_wq2903"
"$INSTALL" "$SRC/lumen_model_watchdog.py" "$WD"
chown lumen:lumen "$WD"

echo "== model-ref-lint + daily timer =="
"$INSTALL" -m 0755 "$SRC/model_ref_lint.py" "$BIN/model-ref-lint"
chown lumen:lumen "$BIN/model-ref-lint"
cp "$SRC/lumen-model-ref-lint.service" "$SRC/lumen-model-ref-lint.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lumen-model-ref-lint.timer

echo "== restart the fleet worker so it re-imports PROVIDER_TO_MODEL =="
systemctl restart lumen-fleet-mistral.service

echo "== first runs =="
systemctl start lumen-model-watchdog.service || true
journalctl -u lumen-model-watchdog.service -n 3 --no-pager -o cat || true
runuser -u lumen -- "$BIN/model-ref-lint" --aq || true
"$0" --verify
