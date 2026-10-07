#!/usr/bin/env bash
# wq#3532 — finish the wq#2094 /home/lumen/projects -> /scratch/fleet/projects migration.
# Option (a): rewrite unit refs to the canonical root, then remove the 4 compat shims
# and the nested projects/projects symlink. See RUNBOOK.md for the decision record.
#
# Run ON Lumen as root:  sudo bash apply.sh          (or: apply.sh rollback <backup-dir>)
# Idempotent: re-running after success is a no-op (precondition check short-circuits).
set -euo pipefail

STAGED="$(cd "$(dirname "$0")/staged" && pwd)"
UNITS=(lumen-bench.service lumen-bench-checker.service lumen-node-killer.service
       lumen-copilot-status.service chrome-mcp.service)
RESTART=(lumen-bench.service lumen-node-killer.service lumen-copilot-status.service)
SHIMS=(bench superpowers-chrome lumen-copilot 03045bd4)
SYSD=/etc/systemd/system
NEWROOT=/scratch/fleet/projects
OLDROOT=/home/lumen/projects
BACKUP="/home/lumen/backups/wq3532-units.$(date +%Y%m%d_%H%M%S)"

fail() { echo "FAIL: $*" >&2; exit 1; }

rollback() {
  local dir="${1:?usage: apply.sh rollback <backup-dir>}"
  [ -d "$dir" ] || fail "no backup dir $dir"
  cp -v "$dir"/*.service "$SYSD/"
  for s in "${SHIMS[@]}"; do
    [ -e "$OLDROOT/$s" ] || ln -s "$NEWROOT/$s" "$OLDROOT/$s"
  done
  systemctl daemon-reload
  systemctl restart "${RESTART[@]}"
  echo "rolled back from $dir (nested $OLDROOT/projects link NOT recreated — it was the bug)"
  exit 0
}
[ "${1:-}" = rollback ] && rollback "${2:-}"

# --- preconditions ----------------------------------------------------------
[ "$(id -u)" = 0 ] || fail "run as root (writes $SYSD, restarts a root unit)"
for u in "${UNITS[@]}"; do [ -f "$STAGED/$u" ] || fail "staged unit missing: $STAGED/$u"; done
grep -rq "$OLDROOT/" "$STAGED" && fail "staged units still contain $OLDROOT — bad stage"
# real targets must exist at the canonical root before we point units at them
for p in bench/checker.py bench/data/bench.db 03045bd4/node_killer.py \
         lumen-copilot/status superpowers-chrome/mcp/dist/index.js; do
  [ -e "$NEWROOT/$p" ] || fail "canonical target missing: $NEWROOT/$p"
done
if ! grep -lq "$OLDROOT/" "$SYSD"/lumen-bench.service 2>/dev/null; then
  echo "live units already migrated (no $OLDROOT refs in $SYSD/lumen-bench.service) — nothing to do"
else
  # --- apply ------------------------------------------------------------------
  mkdir -p "$BACKUP"
  for u in "${UNITS[@]}"; do cp -v "$SYSD/$u" "$BACKUP/"; done
  for u in "${UNITS[@]}"; do install -m 0644 "$STAGED/$u" "$SYSD/$u"; done
  systemctl daemon-reload
  systemctl restart "${RESTART[@]}"
fi

# --- verify (each probe can fail) -------------------------------------------
sleep 3
for u in "${RESTART[@]}"; do
  [ "$(systemctl is-active "$u")" = active ] || fail "$u not active after restart"
done
curl -fsS -o /dev/null http://127.0.0.1:8135/ || fail "bench web app not serving on :8135"
systemctl start lumen-bench-checker.service \
  && [ "$(systemctl show -p ExecMainStatus --value lumen-bench-checker.service)" = 0 ] \
  || fail "bench-checker oneshot exited nonzero (the wq#3473 symptom)"
grep -rln "$OLDROOT/" "$SYSD" && fail "old-path refs remain in $SYSD (listed above)"

# --- remove stopgap (only after verify) --------------------------------------
[ -L "$OLDROOT/projects" ] && rm -v "$OLDROOT/projects"
for s in "${SHIMS[@]}"; do [ -L "$OLDROOT/$s" ] && rm -v "$OLDROOT/$s"; done
[ -L "$OLDROOT" ] && fail "$OLDROOT is itself a symlink — state drifted from the wq#3532 recon, re-audit"

# --- re-verify with shims gone (symlink-masked breakage shows here) ----------
systemctl restart "${RESTART[@]}"; sleep 3
for u in "${RESTART[@]}"; do
  [ "$(systemctl is-active "$u")" = active ] || fail "$u broke once shims were removed"
done
curl -fsS -o /dev/null http://127.0.0.1:8135/ || fail "bench web app broke once shims were removed"

# --- sync fleet-units baseline repo ------------------------------------------
if [ -d /home/lumen/fleet-units/.git ]; then
  for u in "${UNITS[@]}"; do
    f="$(find /home/lumen/fleet-units -name "$u" -not -path '*/.git/*' | head -1)"
    [ -n "$f" ] && cp -v "$SYSD/$u" "$f"
  done
  git -C /home/lumen/fleet-units add -A
  git -C /home/lumen/fleet-units commit -m "wq#3532: units -> /scratch/fleet/projects (migration finished, shims removed)" || true
fi

# --- cron sweep (wq#3532 scan scope the worker sandbox could not reach) -------
echo "--- cron refs to $OLDROOT (fix any hits the same way):"
{ crontab -l -u lumen 2>/dev/null; crontab -l -u root 2>/dev/null; cat /etc/crontab /etc/cron.d/* 2>/dev/null; } \
  | grep -n "$OLDROOT/" || echo "(none)"

echo "OK: migration finished. Backup: $BACKUP  Rollback: sudo bash $0 rollback $BACKUP"
