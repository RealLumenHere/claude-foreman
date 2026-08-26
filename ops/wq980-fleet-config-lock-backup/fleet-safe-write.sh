#!/bin/bash
# fleet-safe-write — serialize + unconditionally back up mutations to live,
# non-git-tracked fleet-config files (instincts.yaml, manifest.yaml,
# instructions/playbooks/*.md, ...).
#
# wq#980: wq960's manual edit to instincts.yaml (no pre-edit backup, deviating
# from change-safety.md G3) was silently clobbered 14 minutes later by wq959's
# apply.sh, which only backs up on first schema migration and holds no lock.
# Two lanes writing the same ungoverned file raced; nothing serialized them
# and the loser's edit left no trace. This wrapper closes both gaps at once:
# an flock so concurrent lanes queue instead of racing, and an unconditional
# G3 backup (verified non-empty) so even a legitimate wholesale overwrite
# (like wq959's) leaves the prior state reconstructable.
#
# Usage:
#   fleet-safe-write <target-file> <label> --copy <source-file>
#   fleet-safe-write <target-file> <label> --cmd  <command> [args...]
#
# <label> tags the backup filename (e.g. wq959, wq960) for provenance.
# --copy installs <source-file> over <target-file>.
# --cmd runs <command...> to mutate <target-file> in place (e.g. sed -i).
#
# Exit codes: 1 = bad usage, 2 = lock not acquired within timeout,
# 3 = pre-edit backup missing/empty (aborts before mutating).
set -euo pipefail

LOCK_TIMEOUT_S=30
LOCK_DIR=/home/lumen/.locks

usage() {
  echo "usage: fleet-safe-write <target-file> <label> --copy <source-file>" >&2
  echo "       fleet-safe-write <target-file> <label> --cmd <command> [args...]" >&2
  exit 1
}

[ $# -ge 3 ] || usage
TARGET="$1"; LABEL="$2"; MODE="$3"; shift 3

mkdir -p "$LOCK_DIR"
LOCKFILE="$LOCK_DIR/$(basename "$TARGET").lock"
exec 9>"$LOCKFILE"
if ! flock -w "$LOCK_TIMEOUT_S" 9; then
  echo "fleet-safe-write: could not acquire lock on $TARGET within ${LOCK_TIMEOUT_S}s — another lane is writing it" >&2
  exit 2
fi

if [ -f "$TARGET" ]; then
  TS="$(date +%Y%m%d_%H%M%S)"
  BACKUP="${TARGET}.bak.${TS}_pre_${LABEL}"
  cp -a "$TARGET" "$BACKUP"
  SIZE="$(stat -c%s "$BACKUP")"
  if [ "$SIZE" -le 0 ]; then
    echo "fleet-safe-write: backup $BACKUP is empty, aborting before mutation" >&2
    exit 3
  fi
  echo "fleet-safe-write: backed up $TARGET -> $BACKUP ($SIZE bytes)"
fi

case "$MODE" in
  --copy)
    [ $# -eq 1 ] || usage
    cp "$1" "$TARGET"
    ;;
  --cmd)
    [ $# -ge 1 ] || usage
    "$@"
    ;;
  *)
    usage
    ;;
esac

echo "fleet-safe-write: wrote $TARGET (label=$LABEL, lock=$LOCKFILE)"
