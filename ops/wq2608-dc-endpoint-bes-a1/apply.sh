#!/usr/bin/env bash
# wq#2608 rollout -- run AS ROOT on Bes. Not runnable from this BUILD worker's
# sandbox: fleet-exec-policy.yaml (harness/fleet-exec-policy.yaml:174,227)
# already allows `ssh(bes)` read-only run + `copy` into /home/bes/lumen-drop
# at the POLICY layer, but this ticket's capability grant (`wq show 2608` ->
# needs: dir(/home/lumen/instructions/drafts) only) never requested either --
# confirmed live: `fleet-exec run bes hostname` -> "DENY
# [grant:grant_missing_env]: grant file did not verify". No root/sudo verb
# exists on fleet-exec at all regardless (same class as wq#2605/wq#2606/
# wq#1698 before it). This is therefore a proposal for a session with real
# access to Bes (Frank, or a future ticket carrying an ssh(bes)+copy(bes)
# grant), same pattern as ops/wq2605-control-plane-isolation/apply.sh and
# ops/wq2606-mcp-gateway-watchdog-a1/apply.sh.
#
# Installs dc-gateway.service (T1 hardening baked in), copies a Bes-local
# dc-enforce-config.py, adds the mcp-bes ingress rule to Bes's EXISTING
# cloudflared config (backed up first), reloads/enables/starts, and prints
# the exact next commands for T2 watchdog wiring + DNS/Access (wq#2614).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

[ "$(id -u)" = 0 ] || { echo "must run as root (unit install + daemon-reload + start)"; exit 1; }

BES_HOME=/home/bes
CFD_CONFIG=/etc/cloudflared/config.yml
DC_UNIT=/etc/systemd/system/dc-gateway.service
HOSTNAME=mcp-bes.lumennode.uk
SERVICE=http://localhost:3100

[ -f "$CFD_CONFIG" ] || {
  echo "REFUSING: $CFD_CONFIG not found -- the ticket's premise (Bes already runs a"
  echo "locally-managed tunnel) does not hold on this box. Verify before re-running."
  exit 1
}

command -v node >/dev/null 2>&1 || {
  echo "Node LTS not found -- design doc §3.2 flags Bes as having no node today."
  echo "Install it first (NodeSource, matching Anubis's wq#2607 recipe), e.g.:"
  echo "  curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -"
  echo "  apt-get install -y nodejs"
  exit 1
}

npm i -g supergateway@3.4.3 @wonderwhy-er/desktop-commander@0.2.46

mkdir -p "$BES_HOME/bin"
# dc-enforce-config.py is canonically /home/lumen/bin/dc-enforce-config.py
# (Lumen's ExecStartPre; lives in the lumen-bin repo, not this one). Its CFG
# path is hardcoded to /home/lumen/... -- generate the Bes-homed copy from
# that single source so the blockedCommands list can never drift between
# nodes.
SRC_DC_ENFORCE=/home/lumen/bin/dc-enforce-config.py
[ -f "$SRC_DC_ENFORCE" ] || { echo "REFUSING: $SRC_DC_ENFORCE not found"; exit 1; }
sed "s#/home/lumen/#${BES_HOME}/#g" "$SRC_DC_ENFORCE" \
  > "$BES_HOME/bin/dc-enforce-config.py"
chmod 0755 "$BES_HOME/bin/dc-enforce-config.py"
chown bes:bes "$BES_HOME/bin/dc-enforce-config.py"

backup() {
  local path="$1"
  [ -e "$path" ] || return 0
  [ -e "${path}.pre-wq2608.bak" ] && return 0
  cp -a "$path" "${path}.pre-wq2608.bak"
  echo "backed up $path -> ${path}.pre-wq2608.bak"
}
backup "$DC_UNIT"

install -m 0644 "$HERE/dc-gateway.service" "$DC_UNIT"

python3 "$HERE/cloudflared_ingress_patch.py" "$CFD_CONFIG" \
  --hostname "$HOSTNAME" --service "$SERVICE"

cloudflared tunnel ingress validate --config "$CFD_CONFIG"

systemctl daemon-reload
systemctl enable --now dc-gateway

sleep 2
hc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/health || echo 000)
echo "dc-gateway post-start health=$hc"
[ "$hc" = 200 ] || {
  echo "HEALTH CHECK FAILED -- roll back with $HERE/rollback.sh"
  exit 1
}

# Quiet-window guard before bouncing cloudflared -- Bes's tunnel carries OTHER
# hostnames' live traffic too (this is the "existing tunnel" the ticket says
# to extend, not a fresh one), same rationale as wq#2605/wq#2606/wq#829.
recent=$(journalctl -u cloudflared --since "5 min ago" --no-pager | grep -c "" || true)
if [ "${recent:-0}" -gt 200 ]; then
  echo "REFUSING cloudflared restart: $recent journal lines in the last 5 min -- looks busy."
  echo "Re-run when quiet, or override with WQ2608_FORCE=1."
  [ "${WQ2608_FORCE:-0}" = 1 ] || exit 1
fi

systemd-run --collect --unit=wq2608-restart-cloudflared systemctl restart cloudflared
for i in $(seq 1 15); do
  sleep 1
  systemctl is-active --quiet cloudflared && break
done

echo "APPLIED. dc-gateway active on :3100 (MemoryHigh=3G MemoryMax=4G, T1 hardening)."
echo "cloudflared now carries an ingress rule for $HOSTNAME -> $SERVICE."
echo
echo "STILL NEEDED (not done by this script):"
echo "  1. T2 watchdog: /home/lumen/foreman/ops/wq2606-mcp-gateway-watchdog-a1/apply.sh"
echo "     with WQ2606_GATEWAY_UNIT=dc-gateway WQ2606_GATEWAY_PORT=3100 (already node-agnostic)."
echo "  2. DNS CNAME + CF Access app: $HERE/dns_access_setup.sh (needs wq#2614's CF token"
echo "     for the Access app; the DNS route may not)."
echo "  3. ssh keys to fleet: $HERE/ssh_fleet_keys.sh"
echo "  4. lumen_state ingest: $HERE/lumen_state_ingest.sh"
