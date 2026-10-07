#!/bin/bash
# wq#3607 — retire 99-lumen-migration-tmp and close the direct-cloudflared-control
# gap left open by lumen's blanket NOPASSWD ALL in /etc/sudoers.d/lumen.
# Run as root via `lumen-run --root` (deploy step, NOT run by the build lane).
#
# Follow-up of wq#3575 (ISSUE-LOG #1221): stage 4 dropped every direct cloudflared
# sudoers grant except /etc/sudoers.d/lumen-cfd-actuator, but lumen still holds
# blanket NOPASSWD ALL via /etc/sudoers.d/lumen and /etc/sudoers.d/99-lumen-migration-tmp,
# so any lumen process can still bypass cfd-actuator and restart/kill/reload
# cloudflared directly. A 30-day auth.log inventory of COMMAND= lines run by
# lumen as root shows the real root workload is wide and ad hoc (smartctl,
# docker, mdadm, apt-get, bash -c, systemd-run wrappers, ...) -- not a finite
# set a sudoers allowlist could safely enumerate in one pass without breaking
# existing ops flows. This bundle therefore does NOT replace the blanket
# grant; it closes the one gap this ticket is about (cloudflared bypassing
# the actuator) with explicit negations layered on top of it, and retires
# the migration-tmp file, which is a dead leftover by name (nothing in this
# repo or 30 days of sudo invocations references it).
#
# Negations live in /etc/sudoers.d/lumen-cfd-actuator (not /etc/sudoers.d/lumen)
# because sudo resolves a user's permission to the LAST matching Cmnd_Spec across
# all loaded files, evaluated in the readdir order of /etc/sudoers.d (plain
# asciibetical: digits < letters), and "lumen-cfd-actuator" sorts after "lumen"
# while "99-lumen-migration-tmp" sorts before it. Editing /etc/sudoers.d/lumen
# directly would require reading it first to avoid clobbering unrelated grants,
# and it is root:root 0440 -- unreadable to the lumen build lane. Extending the
# actuator's own file (which this bundle already owns, stage 0 of wq#1652) needs
# no such read and is guaranteed to land after "lumen" in load order.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
MIGRATION_TMP=/etc/sudoers.d/99-lumen-migration-tmp
ACTUATOR_SUDOERS=/etc/sudoers.d/lumen-cfd-actuator
BACKUP_DIR=/home/lumen/state/wq3607-sudoers-narrow

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  [ -e "$MIGRATION_TMP" ] && _vmiss "still present: $MIGRATION_TMP (should be retired)"
  [ -e "$ACTUATOR_SUDOERS" ] || _vmiss "absent: $ACTUATOR_SUDOERS"
  vdone
fi

install -d -o root -g root -m 700 "$BACKUP_DIR"
STAMP="$(date +%Y%m%dT%H%M%S)"

echo "== retire $MIGRATION_TMP (leftover by name; unreferenced in repo + 30d of sudo invocations) =="
if [ -e "$MIGRATION_TMP" ]; then
  cp -p "$MIGRATION_TMP" "$BACKUP_DIR/99-lumen-migration-tmp.removed-$STAMP"
  rm -f "$MIGRATION_TMP"
else
  echo "already absent"
fi

echo "== extend $ACTUATOR_SUDOERS: deny direct cloudflared control under the blanket grant =="
if [ -e "$ACTUATOR_SUDOERS" ]; then
  cp -p "$ACTUATOR_SUDOERS" "$BACKUP_DIR/lumen-cfd-actuator.pre-wq3607-$STAMP"
fi
install -o root -g root -m 440 "$SRC/lumen-cfd-actuator.sudoers" "$ACTUATOR_SUDOERS"
visudo -cf "$ACTUATOR_SUDOERS"
visudo -cf /etc/sudoers
echo "done"
