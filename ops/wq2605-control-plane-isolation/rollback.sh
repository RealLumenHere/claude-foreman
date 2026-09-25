#!/usr/bin/env bash
# wq#2605 rollback — run AS ROOT. Removes the drop-ins and lumen-workers.slice,
# daemon-reload, restart both control-plane units (detached). Restores the
# .pre-ha.bak unit files if present (belt-and-braces; the drop-ins are additive
# so removing them is normally sufficient without touching the base units).
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "must run as root"; exit 1; }

DC_DROPIN=/etc/systemd/system/desktop-commander-mcp.service.d/10-control-plane-priority.conf
CFD_DROPIN=/etc/systemd/system/cloudflared.service.d/10-control-plane-priority.conf
SLICE_UNIT=/etc/systemd/system/lumen-workers.slice

rm -f "$DC_DROPIN" "$CFD_DROPIN" "$SLICE_UNIT"

for bak in "$DC_DROPIN.pre-ha.bak" "$CFD_DROPIN.pre-ha.bak" "$SLICE_UNIT.pre-ha.bak" \
           /etc/systemd/system/desktop-commander-mcp.service.pre-ha.bak \
           /etc/systemd/system/cloudflared.service.pre-ha.bak; do
  [ -e "$bak" ] || continue
  restore="${bak%.pre-ha.bak}"
  cp -a "$bak" "$restore"
  echo "restored $restore from $bak"
done

systemctl daemon-reload
systemd-run --collect --unit=wq2605-rollback-commander systemctl restart desktop-commander-mcp
systemd-run --collect --unit=wq2605-rollback-cloudflared systemctl restart cloudflared

for i in $(seq 1 15); do
  sleep 1
  systemctl is-active --quiet desktop-commander-mcp && systemctl is-active --quiet cloudflared && break
done

hc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/health || echo 000)
echo "post-rollback health=$hc"
echo "ROLLED BACK. lumen-run still passes --slice=lumen-workers.slice to systemd-run; with the"
echo "persistent unit gone, systemd will auto-create an inert transient slice with no"
echo "CPUWeight/IOWeight/MemoryHigh of its own — jobs land back on default weight. Revert"
echo "bin/lumen-run's --slice edit too if you want that flag gone entirely."
