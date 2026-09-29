#!/usr/bin/env bash
# wq#3108 apply — flip a node to INPUT default-drop with a dead-man rollback.
# Run ON the target node as root, with a reviewed staged dir from genrules.py:
#
#   sudo bash apply.sh arm <staged-dir>      # backup, arm 180s rollback, apply
#   ... verify from Lumen: tailnet ssh, LAN ssh, services (vault: HA health) ...
#   sudo bash apply.sh disarm <staged-dir>   # cancel rollback, persist
#
# If disarm never runs (you got locked out, apply broke something), the transient
# systemd timer restores the pre-change rules at T+180s. Vault pair: launch arm via
# detached systemd-run (instinct 5) so a dropped ssh cannot kill it mid-flight:
#   sudo systemd-run --unit wq3108-apply-$(hostname -s) bash apply.sh arm <staged-dir>
set -euo pipefail

mode="${1:?usage: apply.sh arm|disarm <staged-dir>}"
dir="${2:?usage: apply.sh arm|disarm <staged-dir>}"
unit="wq3108-deadman-$(hostname -s)"

case "$mode" in
arm)
  test -f "$dir/staged.v4" && test -f "$dir/staged.v6" || {
    echo "no staged.v4/staged.v6 in $dir (run genrules.py first)" >&2; exit 2; }
  rm -f "$dir/DISARMED"

  # 0. staged rules must parse before anything else changes
  iptables-restore  --test "$dir/staged.v4"
  ip6tables-restore --test "$dir/staged.v6"

  # 1. backup live rules; the dead-man restores exactly these
  iptables-save  > "$dir/backup.v4"
  ip6tables-save > "$dir/backup.v6"

  # 2. rollback script checks the disarm flag so a late timer firing is a no-op
  printf '%s\n' '#!/usr/bin/env bash' \
    "test -e '$dir/DISARMED' && exit 0" \
    "iptables-restore  < '$dir/backup.v4'" \
    "ip6tables-restore < '$dir/backup.v6'" \
    "logger -t wq3108 'dead-man fired: restored pre-change firewall'" \
    > "$dir/rollback.sh"
  chmod 700 "$dir/rollback.sh"

  # 3. arm the dead-man BEFORE touching live rules (wq#3089 posture)
  systemd-run --unit "$unit" --on-active=180 bash "$dir/rollback.sh"

  # 4. apply
  iptables-restore  "$dir/staged.v4"
  ip6tables-restore "$dir/staged.v6"
  echo "APPLIED. Verify from Lumen NOW (tailnet ssh, LAN ssh, services)."
  echo "Then: sudo bash apply.sh disarm $dir   (before T+180s to avoid rollback)"
  ;;

disarm)
  # flag first — from this instant a firing timer is a no-op; no systemctl needed
  touch "$dir/DISARMED"

  # persist for boot
  mkdir -p /etc/iptables
  iptables-save  > /etc/iptables/rules.v4
  ip6tables-save > /etc/iptables/rules.v6
  if command -v netfilter-persistent >/dev/null 2>&1; then
    netfilter-persistent save
  else
    echo "WARN: netfilter-persistent absent — /etc/iptables/rules.v{4,6} written but" >&2
    echo "WARN: no boot-time restore unit on this node; wire one as a follow-up." >&2
  fi
  echo "DISARMED and persisted. Current INPUT policy:"
  iptables -S INPUT | head -1
  ip6tables -S INPUT | head -1
  ;;

*)
  echo "usage: apply.sh arm|disarm <staged-dir>" >&2; exit 2 ;;
esac
