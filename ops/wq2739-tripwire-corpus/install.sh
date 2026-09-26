#!/bin/bash
# wq#2739 / wq#2749: nothing to install -- this bundle is the re-runnable probe behind
# test_the_mask_suite_does_not_arm_its_own_tripwire. Its live target is the committed
# tree itself, so --verify (ops/VERIFY_CONTRACT.md) scans HEAD of the primary tree and
# exits 0 only when no committed text file carries a whole credential shape. Read-only.
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
echo "wq2739-tripwire-corpus: probe-only bundle, nothing to install (use --verify)"
