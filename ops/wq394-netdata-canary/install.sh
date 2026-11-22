#!/usr/bin/env bash
# wq#394 netdata canary install — reproduces what was applied by hand to `lumen`
# on 2026-08-22. Agent-only (never claims to Netdata Cloud), tailnet-bind only.
#
# Run each step as root (this repo's convention is via lumen-run --root on the
# target host itself; there is no fleet-wide push mechanism here — SSH-based
# fanout is a separate, not-yet-built follow-up, see reports/wq394_netdata_canary.md).
set -euo pipefail

# wq#1696 deploy-verify contract (wq#2985). Read-only, and it must come BEFORE
# the positional-arg line below, which would otherwise swallow --verify as the
# tailscale IP.
if [ "${1:-}" = --verify ]; then
  SRC="$(cd "$(dirname "$0")" && pwd)"
  . "$SRC/../_verify.sh"
  vunit_file "$SRC/wq394-netdata-sampler.service" \
             /etc/systemd/system/wq394-netdata-sampler.service
  vunit_file "$SRC/wq394-netdata-sampler.timer" \
             /etc/systemd/system/wq394-netdata-sampler.timer
  # wq#5275: the 2026-10-08 subtraction (scratch/subtraction/REPORT.md 1G) stopped and
  # disabled netdata.service AND wq394-netdata-sampler.timer on purpose (~408MB RSS; Beszel +
  # gatus are the monitoring of record). Asserting enabled/active on them made --verify fail
  # forever and filed ops-root-apply tickets whose fix (root install) would undo that
  # decision. Same gated shape as wq#5173 (wq1613): run state and the sampler payload are
  # only checked while the operator has the unit enabled; disabled means OFF, but a
  # disabled-yet-running unit is still a MISS (something started it behind the decision).
  _wq394_state() { systemctl is-active "$1" 2>/dev/null || true; }
  if systemctl is-enabled --quiet wq394-netdata-sampler.timer 2>/dev/null; then
    vfile      "$SRC/sampler.py" /usr/local/lib/wq394-netdata-canary/sampler.py
    vfile      "$SRC/analyze.py" /usr/local/lib/wq394-netdata-canary/analyze.py
    venabled   wq394-netdata-sampler.timer
    vactive    wq394-netdata-sampler.timer
    vtimer_scheduled wq394-netdata-sampler.timer  # wq#4910: vactive also passes SubState=elapsed
  else
    case "$(_wq394_state wq394-netdata-sampler.timer)" in
      active|activating) _vmiss "wq394-netdata-sampler.timer disabled but active (subtraction 1G bypassed)" ;;
      *) echo "OFF wq394-netdata-sampler.timer disabled (2026-10-08 subtraction 1G); payload not checked" ;;
    esac
  fi
  if systemctl is-enabled --quiet netdata.service 2>/dev/null; then
    vactive  netdata.service
  else
    case "$(_wq394_state netdata.service)" in
      active|activating) _vmiss "netdata.service disabled but active (subtraction 1G bypassed)" ;;
      *) echo "OFF netdata.service disabled (2026-10-08 subtraction 1G)" ;;
    esac
  fi
  vdone
fi

# wq#5275: installing re-enables netdata + the sampler timer and restarts the soak clock,
# reversing subtraction 1G. That is a decision, not a heal: refuse unless asked for.
if [ "${WQ394_REENABLE:-0}" != 1 ]; then
  echo "install.sh: netdata + wq394-netdata-sampler.timer are intended-off (2026-10-08" \
       "subtraction, scratch/subtraction/REPORT.md 1G). Re-enabling is a decision;" \
       "set WQ394_REENABLE=1 to install anyway." >&2
  exit 2
fi

TAILSCALE_IP="${1:?usage: install.sh <tailscale-ip-of-this-host>}"

# 1. Install (native package via kickstart), agent-only.
curl -sSL https://get.netdata.cloud/kickstart.sh -o /tmp/netdata-kickstart.sh
bash /tmp/netdata-kickstart.sh --non-interactive --stable-channel --disable-cloud --disable-telemetry
# NOTE: kickstart prints "WARNING Cloud cannot be disabled" — this only means the
# cloud connector binary still ships; it stays inert unless netdata-claim.sh is
# run. Do not run netdata-claim.sh on fleet nodes under this ticket's scope.

# 2. Config: bind to tailnet IP only, restrict allow-from to localhost+tailnet,
#    ML anomaly detection on (default in v2.x, set explicit for documentation).
cp /etc/netdata/netdata.conf "/etc/netdata/netdata.conf.orig-wq394"
sed "s/TAILSCALE_IP/${TAILSCALE_IP}/g" "$(dirname "$0")/netdata.conf.template" > /etc/netdata/netdata.conf
chown root:netdata /etc/netdata/netdata.conf
chmod 640 /etc/netdata/netdata.conf
systemctl restart netdata

# 3. Deploy the 48h measurement sampler.
mkdir -p /usr/local/lib/wq394-netdata-canary
cp "$(dirname "$0")/sampler.py" /usr/local/lib/wq394-netdata-canary/sampler.py
cp "$(dirname "$0")/analyze.py" /usr/local/lib/wq394-netdata-canary/analyze.py
chmod 755 /usr/local/lib/wq394-netdata-canary/sampler.py /usr/local/lib/wq394-netdata-canary/analyze.py
cp "$(dirname "$0")/wq394-netdata-sampler.service" /etc/systemd/system/
cp "$(dirname "$0")/wq394-netdata-sampler.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wq394-netdata-sampler.timer

# 4. Mark soak start (analyze.py reads this to gate the 48h window).
mkdir -p /var/lib/wq394-netdata-canary
date +%s > /var/lib/wq394-netdata-canary/soak_start

echo "netdata canary live at http://${TAILSCALE_IP}:19999 (MCP: /mcp)"
echo "MCP dev-preview API key: /var/lib/netdata/mcp_dev_preview_api_key (root-readable)"
echo "soak started; run analyze.py after 48h to get the GO/HOLD recommendation"
