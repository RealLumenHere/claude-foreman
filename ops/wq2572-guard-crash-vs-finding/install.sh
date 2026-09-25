#!/usr/bin/env bash
# wq#2572 Mode C rollout: SuccessExitStatus= drop-ins for four guard units
# (lumen-pat-scan, primary-tree-guard, wq-drift-guard, lumen-retirement-audit)
# plus the two in-repo self-alert/crash-isolation patches (wq523_pat_residue.py,
# prefs_block.py) that make each drop-in safe. See DESIGN.md in this directory.
#
# primary_tree_guard.py's ntfy-send -T/-p fix ships as an ordinary foreman-lane
# commit (harness/primary_tree_guard.py), not a bundle artifact -- verified here
# only as a Mode C prerequisite, never installed by this script.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

# wq#1696 deploy-verify contract (ops/_verify.sh). Read-only; before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile "$SRC/staged/dropin-lumen-pat-scan.conf" \
    /etc/systemd/system/lumen-pat-scan.service.d/60-successexit.conf
  vfile "$SRC/staged/dropin-primary-tree-guard.conf" \
    /etc/systemd/system/primary-tree-guard.service.d/60-successexit.conf
  vfile "$SRC/staged/dropin-wq-drift-guard.conf" \
    /etc/systemd/system/wq-drift-guard.service.d/60-successexit.conf
  vfile "$SRC/staged/dropin-lumen-retirement-audit.conf" \
    /etc/systemd/system/lumen-retirement-audit.service.d/60-successexit.conf
  vcontains /home/lumen/bin/wq523_pat_residue.py 'wq#2572: self-alert independent of systemd state'
  vcontains /home/lumen/minerva/prefs_block.py 'no-probe'
  vcontains /home/lumen/foreman/harness/primary_tree_guard.py '"-T", title'
  vdone
fi

for unit in lumen-pat-scan primary-tree-guard wq-drift-guard lumen-retirement-audit; do
  mkdir -p "/etc/systemd/system/${unit}.service.d"
done
cp "$SRC/staged/dropin-lumen-pat-scan.conf" \
  /etc/systemd/system/lumen-pat-scan.service.d/60-successexit.conf
cp "$SRC/staged/dropin-primary-tree-guard.conf" \
  /etc/systemd/system/primary-tree-guard.service.d/60-successexit.conf
cp "$SRC/staged/dropin-wq-drift-guard.conf" \
  /etc/systemd/system/wq-drift-guard.service.d/60-successexit.conf
cp "$SRC/staged/dropin-lumen-retirement-audit.conf" \
  /etc/systemd/system/lumen-retirement-audit.service.d/60-successexit.conf
systemctl daemon-reload

# Idempotent anchored patches -- each REFUSES (exit 1) instead of double-applying
# if its anchor is already gone (already patched, or the live file changed underneath
# it), so a repeat run is safe to ignore here.
python3 "$SRC/staged/apply_pat_scan_patch.py" || true
python3 "$SRC/staged/apply_prefs_block_patch.py" || true

echo "wq2572 Mode C drop-ins installed; run 'bash $0 --verify' to confirm."
