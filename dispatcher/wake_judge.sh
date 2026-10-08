#!/usr/bin/env bash
# dispatcher/wake_judge.sh — wq#3650: thin entry point for wake_judge.py (see its module
# docstring for the three steps: wakeonlan MAC, poll ssh, dispatch).
#
# Usage: wake_judge.sh <node> [--send-only] [--timeout SECONDS] [--no-dispatch]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$HERE/wake_judge.py" "$@"
