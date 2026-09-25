#!/usr/bin/env bash
# wq#2676 vault ui-drift HA-context annotation. This bundle ships a PROPOSED patch
# (ui-drift.diff) to /home/lumen/bin/ui-drift, not a copy: bin/ is guard.sh-denied to
# direct worker Edit/Write (wq#1719/wq#900), so applying it needs root/write access
# to bin/ that no BUILD worker sandbox has. This script does not install anything.
#
# wq#2683 verify-pass note: the diff's own root-cause framing ("sobek is physically
# down, same class as wq#325/wq#2448") did not hold up under a live probe — sobek
# (192.168.8.178) answers ping and SSH:22, and 8222 gives ECONNREFUSED (host up,
# rejecting), not a timeout (host down). See kb/ISSUE-LOG.md row 784. The patch
# itself (annotate ORIGIN_DOWN findings on HA-managed hosts, never suppress) is
# unaffected by that and still applies cleanly against the live file.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vcontains /home/lumen/bin/ui-drift 'HA_HOSTS = \{"vault.lumennode.uk"'
  vdone
fi

echo "This bundle ships a proposed diff only; it does not install anything."
echo "Apply with write access to /home/lumen/bin:"
echo "  patch -p1 -d /home/lumen/bin < $SRC/ui-drift.diff"
echo "Then re-run: $SRC/install.sh --verify"
exit 1
