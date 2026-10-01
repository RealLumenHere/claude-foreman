#!/bin/bash
# wq#3304: move gocheckin-20260811 + samsung-migration-20260527 (40G) from
# /data/lumen/archive to /mnt/lumen-backup/archive, verify, then delete sources.
# Never delete before verify (Frank pre-approved delete only after a clean verify).
set -euo pipefail

SRC_ROOT="/data/lumen/archive"
DST_ROOT="/mnt/lumen-backup/archive"
DIRS=("gocheckin-20260811" "samsung-migration-20260527")

usage() {
  echo "usage: $0 {copy|verify|delete}" >&2
  exit 1
}

cmd="${1:-}"
[ -n "$cmd" ] || usage

case "$cmd" in
  copy)
    mkdir -p "$DST_ROOT"
    for d in "${DIRS[@]}"; do
      rsync -a --info=progress2 "$SRC_ROOT/$d/" "$DST_ROOT/$d/"
    done
    ;;
  verify)
    fail=0
    for d in "${DIRS[@]}"; do
      # wq#3353: plain `rsync -rc --dry-run` prints nothing at all unless run
      # with -v/-i (see rsync(1), --dry-run), so capturing its stdout into
      # diff_out was always empty and this check always reported OK even on a
      # real diff. -i (itemize-changes) makes rsync actually emit a line per
      # differing/missing/extra file during the dry run.
      diff_out="$(rsync -rci --dry-run --delete "$SRC_ROOT/$d/" "$DST_ROOT/$d/")"
      if [ -n "$diff_out" ]; then
        echo "VERIFY FAILED: diff for $d:" >&2
        echo "$diff_out" >&2
        fail=1
      else
        echo "VERIFY OK: $d (zero rsync -rc diff)"
      fi

      src_sha="$(cd "$SRC_ROOT/$d" && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')"
      dst_sha="$(cd "$DST_ROOT/$d" && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')"
      if [ "$src_sha" != "$dst_sha" ]; then
        echo "VERIFY FAILED: sha256 manifest mismatch for $d ($src_sha != $dst_sha)" >&2
        fail=1
      else
        echo "VERIFY OK: $d sha256 manifest match ($src_sha)"
      fi
    done
    exit "$fail"
    ;;
  delete)
    # Guard: refuse to delete unless verify passes right now.
    "$0" verify
    for d in "${DIRS[@]}"; do
      rm -rf "${SRC_ROOT:?}/${d:?}"
      echo "DELETED SOURCE: $SRC_ROOT/$d"
    done
    ;;
  *)
    usage
    ;;
esac
