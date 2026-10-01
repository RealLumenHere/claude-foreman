#!/usr/bin/env bash
# wq#3289 swap-recycle tool. No install target: swap_recycle.py runs in place
# from this ops dir (see README.md "Run" / "recycle"), it is never copied
# elsewhere and owns no systemd unit. "Live" for this bundle means the script
# parses, its unit tests pass, and its read-only subcommands (top/check) run
# clean against the real box — not that some copy exists under /home/lumen/bin.
#
# wq#3297 deploy-verify contract (ops/VERIFY_CONTRACT.md). Read-only; before
# any install step.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"

  if ! out="$(python3 -m py_compile "$SRC/swap_recycle.py" 2>&1)"; then
    echo "ERR swap_recycle.py fails to compile: $out"
    _V_ERR=1
    vdone
  fi

  if ! out="$(python3 -m unittest discover -s "$SRC" -q 2>&1)"; then
    _vmiss "unit tests fail: $(echo "$out" | tail -5)"
  fi

  check_rc=0
  out="$(python3 "$SRC/swap_recycle.py" check 2>&1)" || check_rc=$?
  # 0 = safe-to-recycle, 1 = correctly reports unsafe; anything else is a crash.
  if [ "$check_rc" -gt 1 ]; then
    echo "ERR check subcommand crashed (exit $check_rc): $out"
    _V_ERR=1
  fi

  if ! out="$(python3 "$SRC/swap_recycle.py" top 1 2>&1)"; then
    echo "ERR top subcommand crashed: $out"
    _V_ERR=1
  fi

  vdone
fi

echo "nothing to install: $SRC/swap_recycle.py runs in place (see README.md)"
