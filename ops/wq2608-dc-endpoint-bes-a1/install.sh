#!/usr/bin/env bash
# wq#2608 Bes DC endpoint bundle. This BUILD worker has no ssh(bes) grant
# (README.md, kb/ISSUE-LOG.md #740's class) so --verify cannot be exercised
# live from this sandbox; it is written for the real `lumen` account, which
# per MCP_CONTROL_PLANE_HA_DESIGN.md §3.2 has real ssh to every fleet node
# (same assumption ops/wq2613-dc-endpoint-parity-check-a1's nightly script
# makes, and the same ssh-probe shape as ops/wq2136-hathor-disable-gpus-unit).
# Read-only: checks dc-gateway.service is active/enabled on Bes and
# byte-identical to what this bundle ships, and that Bes's cloudflared config
# carries the mcp-bes ingress rule cloudflared_ingress_patch.py adds. Never
# installs anything -- see README.md for the actual rollout (needs root on
# Bes + wq#2614 for the Access half).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
NODE=bes
UNIT=dc-gateway.service
CFD_CONFIG=/etc/cloudflared/config.yml
HOSTNAME_RULE=mcp-bes.lumennode.uk

# One round-trip, every command read-only. `|| x=absent` fallback matches
# wq#2136's install.sh: is-active/is-enabled exit nonzero on the very states
# this probe exists to catch (inactive/not-found), so a naive `cmd || x=absent`
# would clobber that real answer -- capture stdout first, default only when
# ssh returned nothing at all for the field.
_probe='u='"$UNIT"'; cfg='"$CFD_CONFIG"'; h='"$HOSTNAME_RULE"';
        e=$(systemctl is-enabled "$u" 2>/dev/null); [ -n "$e" ] || e=absent;
        a=$(systemctl is-active "$u" 2>/dev/null); [ -n "$a" ] || a=absent;
        s=$(sha256sum "/etc/systemd/system/$u" 2>/dev/null | awk "{print \$1}"); [ -n "$s" ] || s=absent;
        c=$(grep -c "$h" "$cfg" 2>/dev/null); [ -n "$c" ] || c=0;
        printf "enabled=%s\nactive=%s\nunit_sha=%s\ningress_count=%s\n" "$e" "$a" "$s" "$c"'

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  out="$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$NODE" "$_probe" 2>/dev/null)" || {
    echo "ERR cannot probe $NODE"; _V_ERR=1
  }
  if [ -n "${out:-}" ]; then
    enabled="$(printf '%s\n' "$out" | sed -n 's/^enabled=//p')"
    active="$( printf '%s\n' "$out" | sed -n 's/^active=//p')"
    unit_sha="$(printf '%s\n' "$out" | sed -n 's/^unit_sha=//p')"
    ingress_count="$(printf '%s\n' "$out" | sed -n 's/^ingress_count=//p')"
    want_sha="$(sha256sum "$SRC/dc-gateway.service" | awk '{print $1}')"

    case "$active"  in active|activating) ;; *) _vmiss "$NODE: $UNIT is-active=$active (want active)";; esac
    case "$enabled" in enabled|static|alias) ;; *) _vmiss "$NODE: $UNIT is-enabled=$enabled (want enabled)";; esac
    [ "$unit_sha" = "$want_sha" ] || _vmiss "$NODE: $UNIT sha256=$unit_sha (want $want_sha, from dc-gateway.service)"
    case "${ingress_count:-0}" in 0) _vmiss "$NODE: $CFD_CONFIG has no ingress rule for $HOSTNAME_RULE";; esac
  fi
  vdone
fi

echo "wq2608-dc-endpoint-bes-a1: nothing to install from this lane; run $SRC/apply.sh as" >&2
echo "root on Bes -- see README.md for the full rollout order." >&2
exit 2
