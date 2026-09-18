#!/usr/bin/env bash
# wq#1968 — rotate LiteLLM virtual keys leaked to Bes logs (wq#1747).
# Thin entrypoint: real logic is in rotate.py (needs urllib/json path
# handling and safe in-place byte rewriting that bash can't do without
# shelling secrets through argv). See README.md for usage and design.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$DIR/rotate.py" "$@"
