#!/usr/bin/env bash
# wq#4984 aq-web systemd unit. Run as a user with sudo on Lumen:
#   bash /home/lumen/foreman/ops/wq4984-aq-web-unit/install.sh
# aq_web.py (127.0.0.1:8766, tailscale serve /alerts) was started by hand with
# nohup on 2026-11-13 and had already died by 2026-11-19. This makes it a real
# enabled unit so it survives crashes and reboots.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
UNITS=(lumen-aq-web.service)
SCRIPT=/home/lumen/agents/alert-queue/aq_web.py

# wq#1696 deploy-verify contract. Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  for u in "${UNITS[@]}"; do vunit_file "$HERE/$u" "/etc/systemd/system/$u"; done
  venabled lumen-aq-web.service
  vactive lumen-aq-web.service
  [ -r "$SCRIPT" ] || _vmiss "absent: $SCRIPT"
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 http://127.0.0.1:8766/open || true)
  [ "$code" = 200 ] || _vmiss "http://127.0.0.1:8766/open answered ${code:-nothing} (want 200)"
  vdone
fi

[ -r "$SCRIPT" ] || { echo "missing $SCRIPT" >&2; exit 1; }
python3 -m py_compile "$SCRIPT"
# Stray hand-started copies (nohup, wq#4984) would hold the port.
pkill -u lumen -f 'python3 .*aq_web\.py' 2>/dev/null || true
sleep 1
for u in "${UNITS[@]}"; do sudo install -m 0644 "$HERE/$u" "/etc/systemd/system/$u"; done
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-aq-web.service
/home/lumen/bin/unit-drift accept --ticket wq#4984 "${UNITS[@]}" || echo "WARN unit-drift accept failed (baseline not blessed)"
sleep 2
systemctl show lumen-aq-web.service -p ActiveState,SubState,MainPID,NRestarts --no-pager
echo "verify:   bash $HERE/install.sh --verify"
echo "rollback: sudo systemctl disable --now lumen-aq-web.service; sudo rm /etc/systemd/system/lumen-aq-web.service; sudo systemctl daemon-reload"
