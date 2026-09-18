#!/usr/bin/env bash
# wq#1990 — install wq#1661's :8871 port-ownership units as root.
#
# wq#1661 (ec1bf29, PR#112) landed deploy/lumen-pokemon-live.service +
# deploy/lumen-kiosk-idle.service + tools/lpp-cover-serve + tools/lpp-cover-maybe-up
# in /home/lumen/pokemon on main, but nothing was ever installed: the running units
# still referenced the superseded /home/lumen/bin/lpp-cover-maybe-up and
# /home/lumen/bin/lpp-idle-guard, so the fix was inert. Procedure below matches
# /home/lumen/pokemon/docs/PORT-8871.md "Installing a change to any of this".
#
# lumen-pokemon-live.service has been LoadState=masked since >= 2026-09-13
# (wq#1895 -- a stale-probe cleanup that found it already masked, cause
# undocumented). wq#1990 decided to unmask as part of this install: the mask
# postdates fire wq#1821 (S4-local :8871 unreachable, still open) and the
# guardian's own heal-ladder note on that fire records a live restart that ran
# but did not recover the stream -- the exact symptom class wq#1661's
# port-ownership contract exists to fix. Leaving the unit masked forever keeps
# the public stream down unconditionally, which is strictly worse than the fire
# it was parked to quiet. If the real restart below does not clear wq#1821,
# that is new evidence for the fire lane, not a reason to re-mask here.
#
# Run as root, detached:
#   /home/lumen/bin/lumen-run start --root --name wq1990-install -- \
#       /home/lumen/worktrees/wq1990-a1/ops/wq1990-pokemon-8871-install/apply.sh
#
# Idempotent: unmask is a no-op if already unmasked; file copies only touch
# the destination when bytes differ; daemon-reload only runs if something
# changed; the old scripts are removed only if still present.
set -euo pipefail

SRC_DIR=/home/lumen/pokemon/deploy
DEST_DIR=/etc/systemd/system
BACKUP_DIR=/home/lumen/backups/wq1990-pokemon-8871
CHANGED=0

mkdir -p "$BACKUP_DIR"

echo "== unmask =="
if systemctl show -P LoadState lumen-pokemon-live.service 2>/dev/null | grep -q '^masked$'; then
    systemctl unmask lumen-pokemon-live.service
    echo "unmasked: lumen-pokemon-live.service"
    CHANGED=1
else
    echo "already unmasked (or not loaded)"
fi

echo "== install unit files =="
for unit in lumen-pokemon-live.service lumen-kiosk-idle.service; do
    src="$SRC_DIR/$unit"
    dest="$DEST_DIR/$unit"
    if [ -f "$dest" ] && cmp -s "$src" "$dest"; then
        echo "unchanged: $dest"
        continue
    fi
    if [ -e "$dest" ] || [ -L "$dest" ]; then
        bak="$BACKUP_DIR/$(date -u +%Y%m%dT%H%M%SZ).${unit}.bak"
        cp -p "$dest" "$bak" 2>/dev/null || echo "note: $dest unreadable for backup (was it a dangling mask symlink?)"
        echo "backup: $bak"
    fi
    install -m 0644 "$src" "$dest"
    echo "installed: $dest"
    CHANGED=1
done

if [ "$CHANGED" = 1 ]; then
    systemctl daemon-reload
    echo "daemon-reload done"
else
    echo "daemon-reload skipped (nothing changed)"
fi

echo "== retire superseded scripts =="
for old in /home/lumen/bin/lpp-cover-maybe-up /home/lumen/bin/lpp-idle-guard; do
    if [ -e "$old" ]; then
        bak="$BACKUP_DIR/$(date -u +%Y%m%dT%H%M%SZ).$(basename "$old").bak"
        cp -p "$old" "$bak"
        rm -f "$old"
        echo "removed (backup $bak): $old"
    else
        echo "already absent: $old"
    fi
done

echo "== final state =="
systemctl show -p LoadState,ActiveState,SubState,UnitFileState lumen-pokemon-live.service lumen-kiosk-idle.service
