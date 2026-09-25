#!/usr/bin/env bash
# wq#2608 rollback -- run AS ROOT on Bes. Undoes apply.sh: stops+disables
# dc-gateway, restores the pre-wq2608 unit/cloudflared-config backups (first
# backup taken = pre-wq2608 baseline, per apply.sh's backup() convention),
# reloads, restarts cloudflared. Does NOT remove the Bes-homed
# dc-enforce-config.py copy or the npm-global packages -- harmless left in
# place, and removing them risks breaking something else on the box that
# happens to share the same npm prefix.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

[ "$(id -u)" = 0 ] || { echo "must run as root"; exit 1; }

DC_UNIT=/etc/systemd/system/dc-gateway.service
CFD_CONFIG=/etc/cloudflared/config.yml

systemctl disable --now dc-gateway 2>/dev/null || true

# apply.sh's backup() only snapshots a path that already existed, and apply.sh
# installs the unit unconditionally -- so no backup means the unit is net-new and
# rollback must delete it. This is NOT symmetric with the CFD_CONFIG block below,
# whose config.yml always pre-exists (see test_rollback_restore_branches.py).
if [ -e "${DC_UNIT}.pre-wq2608.bak" ]; then
  cp -a "${DC_UNIT}.pre-wq2608.bak" "$DC_UNIT"
  echo "restored $DC_UNIT from ${DC_UNIT}.pre-wq2608.bak"
else
  echo "no ${DC_UNIT}.pre-wq2608.bak found -- $DC_UNIT was newly created by apply.sh -- removing it"
  rm -f "$DC_UNIT"
fi

if [ -e "${CFD_CONFIG}.pre-wq2608.bak" ]; then
  cp -a "${CFD_CONFIG}.pre-wq2608.bak" "$CFD_CONFIG"
  echo "restored $CFD_CONFIG from ${CFD_CONFIG}.pre-wq2608.bak"
else
  echo "WARNING: no ${CFD_CONFIG}.pre-wq2608.bak found -- cannot auto-revert the ingress rule."
  echo "Remove the mcp-bes.lumennode.uk ingress entry from $CFD_CONFIG by hand."
fi

systemctl daemon-reload

recent=$(journalctl -u cloudflared --since "5 min ago" --no-pager | grep -c "" || true)
if [ "${recent:-0}" -gt 200 ]; then
  echo "REFUSING cloudflared restart: $recent journal lines in the last 5 min -- looks busy."
  echo "Re-run when quiet, or override with WQ2608_FORCE=1."
  [ "${WQ2608_FORCE:-0}" = 1 ] || exit 1
fi

systemd-run --collect --unit=wq2608-rollback-restart-cloudflared systemctl restart cloudflared
for i in $(seq 1 15); do
  sleep 1
  systemctl is-active --quiet cloudflared && break
done

echo "ROLLED BACK. dc-gateway disabled and its unit restored-or-removed (see above);"
echo "cloudflared config restored (if a backup existed)."
echo "Don't forget the DNS CNAME + CF Access app (dns_access_setup.sh) if either was created --"
echo "this script only touches Bes-local state."
