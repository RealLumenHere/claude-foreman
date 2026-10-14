#!/usr/bin/env bash
# wq#3882 LIMPET flashrom MCP server: no deploy step exists yet. server.py is not
# wired into any systemd unit or the LIMPET meta-server's manifest
# (ops/wq3808-limpet-mcp-meta-server-a1/manifest.example.json) -- README.md's
# "Registering as a meta-server child" section is instructions for a future change,
# not something this bundle does itself. There is nothing installed to byte-match or
# any unit to check is-active, the same situation
# ops/wq3808-limpet-mcp-meta-server-a1/install.sh documents for its own bundle
# (wq#3890 verify-pass).
#
# Two live signals are available instead. First, the mocked test suite -- argv
# construction, chip-line parsing, the confirm=True refusal on erase_chip, the
# missing-binary/timeout/nonzero-exit handling -- actually passes from the primary
# tree. Second, and not covered by any mock in that suite: identify_programmer's real
# subprocess call into ops/wq3809-limpet-hal/hal.py. ops/VERIFY_CONTRACT.md's "payload
# calls another bundle's binary" case applies directly here -- byte-matching this
# bundle's own files proves nothing about whether hal.py's path, argv, or output shape
# still line up, so this handler actually runs that call (no hardware required:
# identify_programmer returning an empty list because nothing is plugged in is a pass;
# hal.py being missing, erroring, or changing its output shape is not).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  if ! python3 -m pytest "$SRC/tests" -q >/dev/null 2>&1; then
    _vmiss "primary-tree test suite fails: ops/wq3882-limpet-flashrom-mcp-a1/tests"
  fi
  if ! python3 -c "import sys; sys.path.insert(0, '$SRC'); import flashrom_ops as ops; ops.identify_programmer()" >/dev/null 2>&1; then
    _vmiss "identify_programmer cannot reach ops/wq3809-limpet-hal/hal.py (real subprocess call, not mocked)"
  fi
  vdone
fi

echo "wq3882-limpet-flashrom-mcp-a1 has no install step yet: server.py is not wired" >&2
echo "into any systemd unit or the LIMPET meta-server's manifest. Registering it as a" >&2
echo "meta-server child (see README.md 'Registering as a meta-server child') is a" >&2
echo "separate change." >&2
echo "verify: bash $SRC/install.sh --verify" >&2
echo "tests:  python3 -m pytest $SRC/tests -q" >&2
exit 0
