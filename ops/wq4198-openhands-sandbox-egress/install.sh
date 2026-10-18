#!/usr/bin/env bash
# wq#4198 lumen-oh-sandbox-guard install. Root, docker-capable lane only, run
# detached so a dropped ssh session cannot leave it half-applied:
#   systemd-run --unit=wq4198-apply --collect bash /home/lumen/foreman/ops/wq4198-openhands-sandbox-egress/install.sh
#   journalctl -u wq4198-apply -f
# Run egress_probe.sh <image> before FIRST (see README.md, Apply).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/services/lumen-oh-sandbox-guard
GUARD="$DEST/sandbox_guard.py"

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile      "$SRC/sandbox_guard.py" "$GUARD"
  vunit_file "$SRC/lumen-oh-sandbox-guard.service" /etc/systemd/system/lumen-oh-sandbox-guard.service
  vfile      "$SRC/../wq2819-iptables-drift-guard/iptables_drift_guard.py" /home/lumen/services/lumen-iptables-drift-guard/iptables_drift_guard.py
  venabled   lumen-oh-sandbox-guard.service
  vactive    lumen-oh-sandbox-guard.service
  vdone
fi

[ "$(id -u)" = 0 ] || { echo "ABORT: needs root"; exit 1; }

# Preflight 1: IPv6 on docker0 would bypass an IPv4-only allowlist.
if ip -6 addr show dev docker0 scope global | grep -q inet6; then
  echo "ABORT: docker0 has a global IPv6 address; an ip6tables mirror is needed first"; exit 1
fi
# Preflight 2: the kernel/iptables accept every match this uses, without touching live rules.
python3 "$SRC/sandbox_guard.py" rules | iptables-restore --noflush --test
python3 "$SRC/sandbox_guard.py" once --dry-run >/dev/null

# Change safety: back up persisted and live rules before any change.
TS="$(date -u +%Y%m%dT%H%M%SZ)"
cp -a /etc/iptables/rules.v4 "/etc/iptables/rules.v4.wq4198.$TS"
iptables-save > "/etc/iptables/live.wq4198.$TS.v4"
echo "backup: /etc/iptables/rules.v4.wq4198.$TS  /etc/iptables/live.wq4198.$TS.v4"

# Drift guard first, so the per-IP exemption churn never pages (wq#2819).
bash "$SRC/../wq2819-iptables-drift-guard/install.sh"

mkdir -p "$DEST"
install -o root -g root -m 755 "$SRC/sandbox_guard.py" "$GUARD"
cp "$SRC/lumen-oh-sandbox-guard.service" /etc/systemd/system/
systemctl daemon-reload

# Static chains, then exemptions + jumps in one reconcile, before the unit
# exists: no docker0 container loses egress on the way in.
python3 "$GUARD" rules | iptables-restore --noflush
python3 "$GUARD" once
systemctl enable --now lumen-oh-sandbox-guard.service

sleep 5
systemctl is-active --quiet lumen-oh-sandbox-guard.service || { echo "FAIL: unit not active; run rollback.sh"; exit 1; }
iptables -C INPUT -i docker0 -j OH-SBX-SRC
iptables -C DOCKER-USER -i docker0 -j OH-SBX-SRC
netfilter-persistent save
python3 /home/lumen/services/lumen-iptables-drift-guard/iptables_drift_guard.py run --dry-run

echo "installed. next: egress_probe.sh <image> after; then a real OpenHands task (README.md, Apply step 5)"
echo "rollback: bash $SRC/rollback.sh $TS"
