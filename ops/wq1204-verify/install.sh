#!/usr/bin/env bash
# wq#1204 verify bundle: probes only, nothing is installed. `--verify` proves the
# mailbot needs_frank classifier still passes the probes that replaced
# probe_needs_frank.py (wq#2171), against the production leakfilter.js.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
MAILBOT=${MAILBOT:-/home/lumen/mailbot}

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  command -v node >/dev/null 2>&1 || { echo "ERR node not found"; exit 2; }
  for p in ops/wq2147-verify/probe.mjs ops/wq2164-verify/probe_pronoun_overfire.mjs; do
    if [ ! -r "$MAILBOT/$p" ]; then
      _vmiss "absent: $MAILBOT/$p"
    elif ! node "$MAILBOT/$p" >/dev/null 2>&1; then
      _vmiss "probe fails: $MAILBOT/$p"
    fi
  done
  vdone
fi

echo "wq1204-verify ships probes only; nothing to install. Run: install.sh --verify"
