#!/usr/bin/env bash
# wq#3522 ser2net bench UART boot-log capture, maksoor ONLY (not Lumen).
# Deploy: scp -r ops/wq3522-ser2net-boot-log-maksoor maksoor:/tmp/ && \
#         ssh maksoor 'sudo bash /tmp/wq3522-ser2net-boot-log-maksoor/install.sh'
#
# CONSTRAINT (ticket wq#3522): the accepter must bind 127.0.0.1 or maksoor's own
# tailnet IP, NEVER 0.0.0.0 -- a serial console is a root shell on the DUT. This
# script resolves the tailnet IP itself (never hardcodes or guesses it) and
# refuses to proceed if tailscale isn't up, rather than ever falling back to a
# wildcard bind. The packaged ser2net.service is disabled immediately after
# install and stays down until our own vetted config is in place -- so there is
# never a window where the stock/default config, if it ships any connection at
# all, is live and reachable. Loopback-tested (test_loopback.sh, via a socat pty
# pair) before the real unit is ever enabled.
#
# Status (2026-10-07): maksoor is currently offline on tailscale (recurring --
# it's Frank's bench RE laptop, power-cycled by hand; see
# reports/wq3544_verify_wq3524_maksoor_ignore_live_deploy_gap.md and
# ops/wq3354-fleet-selfheal/selfheal.yaml's nodes.ignore entry). This bundle
# ships ready to deploy; the next session with live ssh access to maksoor runs
# the scp/ssh line above.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
CONF=/etc/ser2net.yaml
H=maksoor

if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh" 2>/dev/null || . "$HERE/_verify.sh"
  _ssh() { ssh -o BatchMode=yes -o ConnectTimeout=8 "$@"; }
  if [ "$(hostname)" = maksoor ]; then
    ip="$(tailscale ip -4 2>/dev/null | head -1 || true)"
    if [ -z "$ip" ]; then echo "ERR tailscale not up, cannot resolve bind address"; _V_ERR=1; vdone; fi
    rendered="$(mktemp)"
    sed "s/__TAILNET_IP__/$ip/" "$HERE/ser2net.yaml.tmpl" > "$rendered"
    vfile "$rendered" "$CONF"
    rm -f "$rendered"
    venabled ser2net.service
    vactive ser2net.service
    grep -q '0\.0\.0\.0' "$CONF" 2>/dev/null && _vmiss "$CONF binds 0.0.0.0 -- violates wq#3522"
  else
    vremote_up "$H" || vdone
    ip="$(_ssh "$H" "tailscale ip -4" 2>/dev/null | head -1)"
    if [ -z "$ip" ]; then echo "ERR $H: tailscale not up, cannot resolve bind address"; _V_ERR=1; vdone; fi
    rendered="$(mktemp)"
    sed "s/__TAILNET_IP__/$ip/" "$HERE/ser2net.yaml.tmpl" > "$rendered"
    vremote_file "$H" "$rendered" "$CONF"
    rm -f "$rendered"
    vremote_unit "$H" ser2net.service
  fi
  vdone
fi

[ "$(hostname)" = maksoor ] || { echo "refusing: this bundle targets maksoor, not $(hostname)"; exit 1; }

# 1. Install the package but keep it dark until our own config lands.
apt-get update -y
apt-get install -y ser2net socat
systemctl disable --now ser2net.service || true

# 2. Resolve the ONLY address this accepter may bind. Never fall back to 0.0.0.0.
TAILNET_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
if [ -z "$TAILNET_IP" ]; then
  echo "refusing: tailscale is not up on this host, cannot resolve a safe bind address" >&2
  exit 1
fi

# 3. Loopback-test the template before it ever touches a real device or unit.
bash "$HERE/test_loopback.sh" "$HERE/ser2net.yaml.tmpl"

# 4. Render and install our own config -- never the packaged default.
install -d -m 0755 /etc/ser2net
install -d -m 0755 /var/log/ser2net
sed "s/__TAILNET_IP__/$TAILNET_IP/" "$HERE/ser2net.yaml.tmpl" > "$CONF"
chmod 0644 "$CONF"

# 5. Only now bring the service up, bound to $TAILNET_IP, never 0.0.0.0.
systemctl daemon-reload
systemctl enable --now ser2net.service
echo "--- verify ---"
systemctl status ser2net.service --no-pager
ss -tlnp 2>/dev/null | grep ser2net || true
echo "config: $CONF (bound to $TAILNET_IP)"
echo "rollback: systemctl disable --now ser2net.service"
