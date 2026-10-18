#!/usr/bin/env bash
# wq#4198 rollback. Root. Removes the egress allowlist live and from
# rules.v4; leaves the drift guard's wq#4198 filter in place (inert with no
# OH-SBX-SRC chain) and leaves sandbox limits on (harmless; they go away when
# OpenHands recreates the sandbox).
#   bash rollback.sh [<backup-ts>]   # ts from install.sh's "backup:" line
set -uo pipefail
systemctl disable --now lumen-oh-sandbox-guard.service
rm -f /etc/systemd/system/lumen-oh-sandbox-guard.service
systemctl daemon-reload
iptables -D INPUT -i docker0 -j OH-SBX-SRC
iptables -D DOCKER-USER -i docker0 -j OH-SBX-SRC
iptables -F OH-SBX-SRC; iptables -X OH-SBX-SRC
iptables -F OH-SBX-EGRESS; iptables -X OH-SBX-EGRESS
netfilter-persistent save
if [ -n "${1:-}" ]; then
  echo "persisted now vs pre-install backup (expect only dockerd churn):"
  diff <(grep -v '^#' "/etc/iptables/rules.v4.wq4198.$1" | sed 's/\[[0-9]*:[0-9]*\]/[0:0]/') \
       <(grep -v '^#' /etc/iptables/rules.v4 | sed 's/\[[0-9]*:[0-9]*\]/[0:0]/')
fi
iptables -S | grep -c OH-SBX && echo "WARN: OH-SBX rules remain" || echo "rolled back: no OH-SBX rules live"
