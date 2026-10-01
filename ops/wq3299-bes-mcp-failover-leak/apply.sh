#!/usr/bin/env bash
# wq#3299 apply — ship Lumen's patched supergateway dist (narrowed per-request
# reap, upstream #108) to Bes, install the MemoryHigh/MemoryMax/TasksMax
# drop-in and the mcp-pressure-guard-bes timer, and restart bes-mcp-failover
# with a dead-man rollback.
#
# Run ON Bes as root, from this staged dir (already rsynced here alongside the
# patched dist tree fetched from Lumen's ~/.npm-global/lib/node_modules/supergateway):
#
#   sudo systemd-run --unit wq3299-apply-bes bash apply.sh arm <staged-dist-dir>
#   ... verify from Lumen: curl mcp-failover.lumennode.uk health, HA still up ...
#   sudo bash apply.sh disarm <staged-dist-dir>
#
# If disarm never runs (box went bad), the transient timer restores the
# pre-change supergateway dist + unit config at T+180s and restarts the unit
# again on the known-good version. Restart itself is safe (service is
# stateless per request, confirmed in the ticket), so the risk here is a BAD
# dist copy, not the restart.
set -euo pipefail

mode="${1:?usage: apply.sh arm|disarm <staged-dist-dir>}"
dir="${2:?usage: apply.sh arm|disarm <staged-dist-dir>}"
unit="bes-mcp-failover"
deadman="wq3299-deadman-bes"
live_dist="/home/bes/.npm-global/lib/node_modules/supergateway/dist"
dropin_dir="/etc/systemd/system/${unit}.service.d"
guard_bin="/usr/local/bin/mcp-pressure-guard-bes.sh"

case "$mode" in
arm)
  test -f "$dir/package.json" && test -d "$dir/gateways" || {
    echo "no package.json/gateways in $dir (expected a supergateway dist tree)" >&2; exit 2; }
  rm -f "$dir/DISARMED"

  # 0. versions must match before anything else changes (ticket: "same version, check first")
  want=$(grep -m1 '"version"' "$dir/package.json" | sed -E 's/.*: *"([^"]+)".*/\1/')
  have=$(grep -m1 '"version"' "$live_dist/package.json" | sed -E 's/.*: *"([^"]+)".*/\1/')
  if [ "$want" != "$have" ]; then
    echo "version mismatch: staged=$want live=$have — re-check before patching dist in place" >&2
    exit 2
  fi

  # 1. backup live dist + unit config; the dead-man restores exactly these
  rm -rf "$dir/backup-dist"
  cp -a "$live_dist" "$dir/backup-dist"
  mkdir -p "$dir/backup-dropin"
  cp -a "$dropin_dir"/. "$dir/backup-dropin/" 2>/dev/null || true

  # 2. rollback script checks the disarm flag so a late timer firing is a no-op
  printf '%s\n' '#!/usr/bin/env bash' \
    "test -e '$dir/DISARMED' && exit 0" \
    "rm -rf '$live_dist'" \
    "cp -a '$dir/backup-dist' '$live_dist'" \
    "rm -rf '$dropin_dir'" \
    "mkdir -p '$dropin_dir'" \
    "cp -a '$dir/backup-dropin/.' '$dropin_dir/' 2>/dev/null || true" \
    "systemctl daemon-reload" \
    "systemctl restart '$unit'" \
    "logger -t wq3299 'dead-man fired: restored pre-change supergateway dist + unit config on bes'" \
    > "$dir/rollback.sh"
  chmod 700 "$dir/rollback.sh"

  systemd-run --unit "$deadman" --on-active=180 bash "$dir/rollback.sh"

  # 3. apply: copy patched dist, install drop-in + guard script + timer, restart
  rm -rf "$live_dist"
  cp -a "$dir" "$live_dist.tmp"
  rm -rf "$live_dist.tmp/backup-dist" "$live_dist.tmp/backup-dropin" "$live_dist.tmp/rollback.sh" "$live_dist.tmp/DISARMED"
  mv "$live_dist.tmp" "$live_dist"

  mkdir -p "$dropin_dir"
  cp "$dir/../bes-mcp-failover.service.d/99-wq3299-memlimit.conf" "$dropin_dir/" 2>/dev/null \
    || cp "$(dirname "$0")/bes-mcp-failover.service.d/99-wq3299-memlimit.conf" "$dropin_dir/"
  cp "$(dirname "$0")/mcp-pressure-guard-bes.sh" "$guard_bin"
  chmod 755 "$guard_bin"
  cp "$(dirname "$0")/mcp-pressure-guard-bes.service" /etc/systemd/system/
  cp "$(dirname "$0")/mcp-pressure-guard-bes.timer" /etc/systemd/system/

  systemctl daemon-reload
  pid_before=$(systemctl show "$unit" -p MainPID --value)
  systemctl restart "$unit"
  sleep 3
  pid_after=$(systemctl show "$unit" -p MainPID --value)
  if [ -z "$pid_after" ] || [ "$pid_after" = "0" ] || [ "$pid_after" = "$pid_before" ]; then
    echo "MainPID did not advance ($pid_before -> $pid_after) — leaving dead-man armed, NOT disarming" >&2
    exit 1
  fi
  systemctl enable --now mcp-pressure-guard-bes.timer
  echo "applied: $unit MainPID $pid_before -> $pid_after. Verify health, then: bash apply.sh disarm $dir"
  ;;
disarm)
  touch "$dir/DISARMED"
  systemctl stop "$deadman" 2>/dev/null || true
  echo "disarmed: $deadman cancelled, change persisted"
  ;;
*)
  echo "usage: apply.sh arm|disarm <staged-dist-dir>" >&2
  exit 2
  ;;
esac
