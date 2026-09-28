#!/bin/bash
# wq#2739 / wq#2749: --verify is the re-runnable probe behind
# test_the_mask_suite_does_not_arm_its_own_tripwire. Its live target is the committed
# tree itself, so --verify (ops/VERIFY_CONTRACT.md) scans HEAD of the primary tree and
# exits 0 only when no committed text file carries a whole credential shape. Read-only.
#
# wq#2981: --install-hook wires the pre-commit half in -- scan_tree.py --staged run
# against the INDEX before `git commit` writes it, so the file that would have armed
# --verify (or the pytest gate) after landing is refused before it lands instead
# (ISSUE-LOG #951). Writes one file, $(git rev-parse --git-common-dir)/hooks/pre-commit
# -- shared by every worktree of the target repo. Refuses to clobber a pre-commit hook
# this script did not write (identified by its own wq#2981 marker line); re-run with
# --install-hook --force to replace it anyway.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SRC/../.." && pwd)"

if [ "${1:-}" = --verify ]; then
  out="$(python3 "$SRC/scan_tree.py" "$REPO" HEAD 2>&1)" || {
    echo "$out" | sed -n '/^offenders/,$p' | sed 's/^  /MISS /'
    exit 1
  }
  echo "$out" | head -2
  echo "OK tripwire corpus clean at $(git -C "$REPO" rev-parse --short HEAD)"
  exit 0
fi

if [ "${1:-}" = --install-hook ]; then
  shift
  target_repo="$REPO"
  force=false
  for arg in "$@"; do
    case "$arg" in
      --force) force=true ;;
      *) target_repo="$arg" ;;
    esac
  done
  common_dir="$(git -C "$target_repo" rev-parse --git-common-dir)"
  case "$common_dir" in
    /*) : ;;
    *) common_dir="$target_repo/$common_dir" ;;
  esac
  dest="$common_dir/hooks/pre-commit"
  if [ -e "$dest" ] && ! grep -q 'wq#2981' "$dest" 2>/dev/null && [ "$force" != true ]; then
    echo "refusing to overwrite existing hook at $dest (not ours) -- pass --force to replace" >&2
    exit 1
  fi
  install -m 0755 "$SRC/pre-commit" "$dest"
  echo "installed $dest"
  exit 0
fi

echo "wq2739-tripwire-corpus: use --verify (scan HEAD) or --install-hook [<repo>] (wire the pre-commit gate)"
