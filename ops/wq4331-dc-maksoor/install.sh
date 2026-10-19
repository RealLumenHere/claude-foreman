#!/usr/bin/env bash
# wq#4331 -- Desktop Commander MCP on Maksoor (RE workstation), direct tailnet access
# for agents: mtkclient/ADB/rkdeveloptool/etc. run ON the box with the target device
# plugged in, no SSH-hop through Lumen first. Same shape as the Anubis/Bes/P1 DC
# endpoints (ops/wq2607-dc-endpoint-anubis, ops/wq2608-dc-endpoint-bes-a1,
# ops/p1-commander) and the same dual local/remote --verify handler already used for
# Maksoor's other bundle (ops/wq3522-ser2net-boot-log-maksoor/install.sh).
#
# Deploy: scp -r ops/wq4331-dc-maksoor maksoor:/tmp/ && \
#         ssh maksoor 'sudo bash /tmp/wq4331-dc-maksoor/install.sh'
#
# Status: NO-PROBE, shipped undeployed. This BUILD worker's Bash allowlist has no
# ssh/scp entry at all (harness/worker-settings.json; same class as
# kb/ISSUE-LOG.md #740/#1055b -- wq#3151's deploy-ccc-on-maksoor hit the identical
# wall), so neither --verify nor the install itself can be exercised live from this
# sandbox. wq#4333 re-checked the fleet-exec path too: maksoor IS in
# fleet-exec-policy.yaml's allow.ssh (wq#3857), but (a) this ticket carries no
# needs:ssh(maksoor) tag so fleet-exec denies it outright
# (`DENY [grant:grant_missing_env]: grant file did not verify`), and (b) even
# granted, that entry is read-only `run` under run_readonly_cmds only -- no
# copy_dirs[maksoor] and the `sudo` verb is still v2/unimplemented
# (fleet-exec-policy.yaml:230-238) -- so fleet-exec cannot substitute for raw
# ssh/scp here regardless of grant. The real lumen account (or Frank, or a future
# ticket carrying an ssh(maksoor) grant) runs the scp/ssh line above. See README.md.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
H=maksoor
USER_HOME=/home/maksoor
UNIT=dc-gateway.service
SG_VER=3.4.3
DC_VER=0.2.46

if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh" 2>/dev/null || . "$HERE/_verify.sh"
  if [ "$(hostname)" = "$H" ]; then
    vfile "$HERE/dc-gateway.service" "/etc/systemd/system/$UNIT"
    vfile "$HERE/dc-gateway-hardening.conf" "/etc/systemd/system/$UNIT.d/dc-gateway-hardening.conf"
    vfile "$HERE/dc-gateway-logging.conf" "/etc/systemd/system/$UNIT.d/dc-gateway-logging.conf"
    vfile "$HERE/dc-enforce-config.py" "$USER_HOME/bin/dc-enforce-config.py"
    venabled "$UNIT"
    vactive "$UNIT"
  else
    vremote_up "$H" || vdone
    vremote_file "$H" "$HERE/dc-gateway.service" "/etc/systemd/system/$UNIT"
    vremote_file "$H" "$HERE/dc-gateway-hardening.conf" "/etc/systemd/system/$UNIT.d/dc-gateway-hardening.conf"
    vremote_file "$H" "$HERE/dc-gateway-logging.conf" "/etc/systemd/system/$UNIT.d/dc-gateway-logging.conf"
    vremote_file "$H" "$HERE/dc-enforce-config.py" "$USER_HOME/bin/dc-enforce-config.py"
    vremote_unit "$H" "$UNIT"
  fi
  vdone
fi

[ "$(hostname)" = "$H" ] || { echo "refusing: this bundle targets maksoor, not $(hostname)"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "must run as root (unit install + daemon-reload + enable)"; exit 1; }

command -v node >/dev/null 2>&1 || {
  echo "Node LTS not found on maksoor -- install it first (NodeSource, same recipe"
  echo "already used for Anubis/Bes):"
  echo "  curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -"
  echo "  apt-get install -y nodejs"
  exit 1
}

echo "== npm globals: supergateway@${SG_VER} @wonderwhy-er/desktop-commander@${DC_VER} (pinned to the existing fleet DC endpoints) =="
sudo -u "$H" env PUPPETEER_SKIP_DOWNLOAD=true npm --prefix "$USER_HOME/.npm-global" install -g \
  "supergateway@${SG_VER}" "@wonderwhy-er/desktop-commander@${DC_VER}"

mkdir -p "$USER_HOME/bin" "$USER_HOME/logs/supergateway"
install -o "$H" -g "$H" -m 0600 /dev/null "$USER_HOME/logs/supergateway/dc-gateway.log"
install -m 0755 "$HERE/dc-enforce-config.py" "$USER_HOME/bin/dc-enforce-config.py"
chown "$H:$H" "$USER_HOME/bin/dc-enforce-config.py"

backup() {
  local path="$1"
  [ -e "$path" ] || return 0
  [ -e "${path}.pre-wq4331.bak" ] && return 0
  cp -a "$path" "${path}.pre-wq4331.bak"
  echo "backed up $path -> ${path}.pre-wq4331.bak"
}
backup "/etc/systemd/system/$UNIT"

install -m 0644 "$HERE/dc-gateway.service" "/etc/systemd/system/$UNIT"
install -d -m 0755 "/etc/systemd/system/$UNIT.d"
install -m 0644 "$HERE/dc-gateway-hardening.conf" "/etc/systemd/system/$UNIT.d/dc-gateway-hardening.conf"
install -m 0644 "$HERE/dc-gateway-logging.conf" "/etc/systemd/system/$UNIT.d/dc-gateway-logging.conf"

systemctl daemon-reload
systemctl enable --now "$UNIT"

sleep 2
hc=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3100/health || echo 000)
echo "dc-gateway post-start health=$hc"
[ "$hc" = 200 ] || {
  echo "HEALTH CHECK FAILED -- rollback: systemctl disable --now $UNIT; restore ${UNIT}.pre-wq4331.bak if present"
  exit 1
}

TAILNET_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
echo "APPLIED. dc-gateway active on :3100 (MemoryHigh=2G MemoryMax=3G, T1 hardening, secrets-at-rest logging)."
echo "Agents reach Maksoor directly at ${TAILNET_IP:-<tailscale ip -4>}:3100/mcp -- no SSH hop through Lumen."
echo
echo "STILL NEEDED (not done by this script):"
echo "  1. lumen_state.py ingest: record maksoor.dc_endpoint = dc-gateway.service active on :3100."
echo "  2. Point the agent-side MCP client config at this endpoint for overnight RE sessions."
