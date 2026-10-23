#!/usr/bin/env bash
# wq#4485 apply — Thor SSH hardening (sshd drop-in + UFW + fail2ban) with a
# dead-man rollback, same posture as ops/wq3108-fleet-input-default-drop/apply.sh.
#
# Thor (100.112.151.100) is tier-0 hands-off and never targeted by fleet
# automation (fleet-config/README.md, fleet-config/docs/ARCHITECTURE.md,
# fleet-config/ansible/playbooks/site.yml) — this script is NOT wired into any
# playbook or orchestrator path. It is meant to be run BY FRANK, at Thor's own
# console/local shell, never dispatched remotely by a worker:
#
#   sudo bash apply.sh arm <staged-dir>      # backup, arm 180s rollback, apply
#   ... verify: a NEW ssh session with a key still works, sudo still works ...
#   sudo bash apply.sh disarm <staged-dir>   # cancel rollback, persist
#
# <staged-dir> holds this directory's 99-lumen.conf and fail2ban-jail.local.
# If disarm never runs (locked out, apply broke something), the transient
# systemd timer restores the pre-change sshd config + ufw rules at T+180s.
set -euo pipefail

mode="${1:?usage: apply.sh arm|disarm <staged-dir>}"
dir="${2:?usage: apply.sh arm|disarm <staged-dir>}"
unit="wq4485-deadman-$(hostname -s)"
sshd_drop="/etc/ssh/sshd_config.d/99-lumen.conf"

case "$mode" in
arm)
  test -f "$dir/99-lumen.conf" && test -f "$dir/fail2ban-jail.local" || {
    echo "no 99-lumen.conf/fail2ban-jail.local in $dir" >&2; exit 2; }
  rm -f "$dir/DISARMED"

  # 0. staged sshd drop-in must parse before anything else changes
  sshd -t -f <(cat /etc/ssh/sshd_config; echo "Include $dir/99-lumen.conf")

  # 1. backup live state; the dead-man restores exactly this
  test -f "$sshd_drop" && cp "$sshd_drop" "$dir/backup.sshd.conf" || rm -f "$dir/backup.sshd.conf"
  ufw status verbose > "$dir/backup.ufw.status" 2>&1 || true

  # 2. rollback script checks the disarm flag so a late timer firing is a no-op
  printf '%s\n' '#!/usr/bin/env bash' \
    "test -e '$dir/DISARMED' && exit 0" \
    "if test -f '$dir/backup.sshd.conf'; then cp '$dir/backup.sshd.conf' '$sshd_drop'; else rm -f '$sshd_drop'; fi" \
    "systemctl reload ssh || systemctl reload sshd" \
    "ufw disable" \
    "logger -t wq4485 'dead-man fired: restored pre-change sshd config, disabled ufw'" \
    > "$dir/rollback.sh"
  chmod 700 "$dir/rollback.sh"

  # 3. arm the dead-man BEFORE touching live sshd/ufw (wq#3089 posture)
  systemd-run --unit "$unit" --on-active=180 bash "$dir/rollback.sh"

  # 4. apply: sshd drop-in, UFW (allow rules BEFORE enable), fail2ban
  install -m 644 "$dir/99-lumen.conf" "$sshd_drop"
  systemctl reload ssh || systemctl reload sshd

  ufw allow in on tailscale0
  ufw allow OpenSSH
  ufw --force enable

  apt-get install -y fail2ban
  install -m 644 "$dir/fail2ban-jail.local" /etc/fail2ban/jail.local
  systemctl enable --now fail2ban

  echo "APPLIED. Verify NOW: open a NEW ssh session (key auth) and confirm sudo works."
  echo "Then: sudo bash apply.sh disarm $dir   (before T+180s to avoid rollback)"
  ;;

disarm)
  # flag first — from this instant a firing timer is a no-op; no systemctl needed
  touch "$dir/DISARMED"
  echo "DISARMED. Current state:"
  ufw status verbose
  systemctl is-enabled fail2ban
  ;;

*)
  echo "usage: apply.sh arm|disarm <staged-dir>" >&2; exit 2 ;;
esac
