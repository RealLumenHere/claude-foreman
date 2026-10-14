#!/usr/bin/env bash
# wq#2744 P1 Commander node bring-up. Run AS ROOT, ON the P1 node itself,
# AFTER Armbian is flashed+booted+reachable over SSH as a sudo user (see
# README.md "OS bring-up" -- this script does NOT flash or image anything).
#
# This script only ever touches the P1 node it runs on. It does not touch
# Lumen, mcpw, DNS, or CF Access -- those stay under Frank-driven control
# per the CONTROL-PLANE HARD RULES (claude_memory.md, 2026-09-25) and are
# covered in mcpw-cutover-plan.md, run separately, later, once both P1
# boxes are verified healthy standalone.
#
# Usage:
#   sudo ./install.sh --role primary --hostname p1-primary
#   sudo ./install.sh --role backup  --hostname p1-backup
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

ROLE=""
NODE_USER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --role) ROLE=${2:-}; shift 2 ;;
    --hostname) NODE_USER=${2:-}; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
case "$ROLE" in primary|backup) ;; *) echo "error: --role must be 'primary' or 'backup'" >&2; exit 2 ;; esac
[ -n "$NODE_USER" ] || { echo "error: --hostname required (e.g. p1-primary)" >&2; exit 2; }
[ "$(id -u)" = 0 ] || { echo "must run as root" >&2; exit 1; }

echo "=== sanity checks ==="
ARCH=$(uname -m)
if [ "$ARCH" != "aarch64" ] && [ "$ARCH" != "arm64" ]; then
  echo "WARNING: uname -m reports '$ARCH', not aarch64/arm64 -- is this really the P1 box?"
  read -r -p "Continue anyway? [y/N] " ans
  [ "${ans:-N}" = "y" ] || exit 1
fi
RAM_GB=$(awk '/MemTotal/{printf "%.1f", $2/1024/1024}' /proc/meminfo)
echo "arch=$ARCH  RAM=${RAM_GB}G  role=$ROLE  node_user=$NODE_USER"
id "$NODE_USER" >/dev/null 2>&1 || { echo "error: user $NODE_USER does not exist on this box yet -- create it first (matches fleet SSH-username-per-hostname convention)"; exit 1; }

echo "=== Node.js LTS (arm64) ==="
if ! command -v node >/dev/null 2>&1 || [ "$(node -v | sed 's/^v//;s/\..*//')" -lt 22 ]; then
  curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -
  apt-get install -y nodejs
fi
node -v; npm -v
echo "$(node -v)" | grep -q "^v" || { echo "node install failed"; exit 1; }

echo "=== npm globals: supergateway@3.4.3 @wonderwhy-er/desktop-commander@0.2.46 (pinned to Lumen's live versions) ==="
# PUPPETEER_SKIP_DOWNLOAD: desktop-commander's md-to-pdf dependency pulls in
# puppeteer, whose bundled @puppeteer/browsers@3.0.6 maps Linux-arm64 to the
# SAME 'linux64' (x86_64) download as plain Linux for both full Chrome and
# chrome-headless-shell (verified by reading the actual installed code in
# node_modules/@puppeteer/browsers/lib/browser-data/{chrome,chrome-headless-shell}.js
# on Lumen, 2026-09-26 -- this is not a guess). The postinstall swallows the
# download error and exits 0 regardless, so an unset SKIP flag means npm
# install SILENTLY succeeds while leaving an x86_64 binary that will throw
# "exec format error" the first time anyone triggers DC's markdown-to-PDF
# tool on this arm64 box. None of the Commander MCP tools Claude actually
# uses (start_process/read_file/write_file/edit_block/list_directory/search)
# touch Puppeteer -- this only affects the PDF-conversion capability, which
# is out of scope for a control-plane node. Skipping the download makes that
# limitation visible and clean at install time instead of a deferred crash.
sudo -u "$NODE_USER" env PUPPETEER_SKIP_DOWNLOAD=true npm config set prefix "/home/$NODE_USER/.npm-global" --global 2>/dev/null || true
sudo -u "$NODE_USER" mkdir -p "/home/$NODE_USER/.npm-global"
sudo -u "$NODE_USER" env PUPPETEER_SKIP_DOWNLOAD=true npm --prefix "/home/$NODE_USER/.npm-global" install -g supergateway@3.4.3 @wonderwhy-er/desktop-commander@0.2.46

echo "=== dc-enforce-config.py + dc-gateway unit + drop-ins ==="
sudo -u "$NODE_USER" mkdir -p "/home/$NODE_USER/bin" "/home/$NODE_USER/logs/supergateway" "/home/$NODE_USER/.secrets"
install -o "$NODE_USER" -g "$NODE_USER" -m 0755 "$HERE/dc-enforce-config.py" "/home/$NODE_USER/bin/dc-enforce-config.py"
install -o "$NODE_USER" -g "$NODE_USER" -m 0600 /dev/null "/home/$NODE_USER/logs/supergateway/dc-gateway.log"

sed "s#__NODE_USER__#${NODE_USER}#g" "$HERE/dc-gateway.service" > /etc/systemd/system/dc-gateway.service
install -D -m 0644 "$HERE/dc-gateway-hardening.conf" /etc/systemd/system/dc-gateway.service.d/dc-gateway-hardening.conf
sed "s#__NODE_USER__#${NODE_USER}#g" "$HERE/dc-gateway-logging.conf" > /etc/systemd/system/dc-gateway.service.d/dc-gateway-logging.conf
sed "s#__NODE_USER__#${NODE_USER}#g" "$HERE/supergateway-logrotate.conf" > /etc/logrotate.d/supergateway

echo "=== watchdog (mcp + cloudflared + mcpw + dcredact probes) ==="
install -o "$NODE_USER" -g "$NODE_USER" -m 0755 "$HERE/mcp-gateway-watchdog.sh" "/home/$NODE_USER/bin/mcp-gateway-watchdog.sh"
install -o "$NODE_USER" -g "$NODE_USER" -m 0755 "$HERE/dc-redaction-gate.sh" "/home/$NODE_USER/bin/dc-redaction-gate.sh"   # wq#3804
sed "s#__NODE_USER__#${NODE_USER}#g" "$HERE/mcp-gateway-watchdog.service" > /etc/systemd/system/mcp-gateway-watchdog.service
install -D -m 0644 "$HERE/mcp-gateway-watchdog.timer" /etc/systemd/system/mcp-gateway-watchdog.timer
install -D -m 0644 "$HERE/cloudflared-metrics.conf" /etc/systemd/system/cloudflared.service.d/wq2606-metrics.conf

echo "=== sudoers: watchdog restart rights, scoped to exactly two units ==="
cat > /etc/sudoers.d/mcp-gateway-watchdog <<SUDOERS
${NODE_USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart dc-gateway, /usr/bin/systemctl restart cloudflared
SUDOERS
chmod 0440 /etc/sudoers.d/mcp-gateway-watchdog
visudo -cf /etc/sudoers.d/mcp-gateway-watchdog

systemctl daemon-reload
systemctl enable --now dc-gateway
systemctl enable --now mcp-gateway-watchdog.timer

echo
echo "=== APPLIED on $(hostname) (role=$ROLE, user=$NODE_USER) ==="
echo "dc-gateway:              $(systemctl is-active dc-gateway || true)"
echo "mcp-gateway-watchdog.timer: $(systemctl is-active mcp-gateway-watchdog.timer || true)"
curl -s -m 5 -o /dev/null -w 'local /health = %{http_code}\n' http://127.0.0.1:3100/health || true
echo
echo "REMAINING STEPS (not done by this script -- see README.md for full detail):"
echo "  1. cloudflared: apt-get install cloudflared (arm64, via pkg.cloudflare.com repo -- see README.md);"
echo "     'cloudflared tunnel login' (interactive, Frank) THEN 'cloudflared tunnel create ${NODE_USER}-mcp'"
echo "     run ON THIS BOX (never on Lumen -- see cloudflared-ingress-${NODE_USER}.yml header)."
echo "     Merge that file's ingress: block into this box's /etc/cloudflared/config.yml,"
echo "     enable --metrics localhost:20241 (cloudflared-metrics.conf drop-in already installed),"
echo "     systemctl enable --now cloudflared."
echo "  2. DNS CNAME mcp-${NODE_USER}.lumennode.uk -> <this tunnel's uuid>.cfargotunnel.com"
echo "     + CF Access app on that hostname, policy = reuse existing 'Worker service token'"
echo "     e23d092b-c2d2-4093-b1ec-ecd1a08ec2c0 (same pattern as mcp-anubis/mcp-failover)."
echo "  3. SSH keys: generate /home/${NODE_USER}/.ssh/id_ed25519, distribute the pubkey to"
echo "     lumen + every fleet node's authorized_keys, so a session landing here during a"
echo "     Lumen outage can still run standard SSH-wrapped fleet commands."
echo "  4. mcpw watchdog token: complete one OAuth round-trip against mcpw (mcpw-diag.py"
echo "     in this directory) and save the resulting bearer token to"
echo "     /home/${NODE_USER}/.secrets/mcpw-watchdog.token, mode 0600."
echo "  5. lumen_state.py ingest ${NODE_USER} <fields> --source wq2744 ; state-regen (from Lumen)."
echo "  6. static LAN IP via netplan (fleet standard -- no node stays on DHCP)."
echo "  7. Only once BOTH boxes pass steps 1-6 independently: mcpw-cutover-plan.md."
