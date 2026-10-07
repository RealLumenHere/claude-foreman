#!/usr/bin/env bash
# wq#3430 TF1 5-appliance role plan. No install target: render_role.py and
# roles.yaml are config-only (README.md "What ships in this ticket") with no
# fleet-exec grant yet for these units (README.md's own blocker), so nothing
# here is copied onto a box or owns a systemd unit. "Live" for this bundle
# means the role schema parses, validate_all() accepts all 5 roles, and the
# shipped test suite passes — not that any role has actually been applied to
# hardware, which this worktree has no way to check.
#
# wq#3574 deploy-verify contract (ops/VERIFY_CONTRACT.md). Read-only; before
# any install step.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"

  if ! out="$(python3 -m py_compile "$SRC/render_role.py" 2>&1)"; then
    echo "ERR render_role.py fails to compile: $out"
    _V_ERR=1
    vdone
  fi

  if ! out="$(cd "$SRC" && python3 render_role.py --validate 2>&1)"; then
    _vmiss "render_role.py --validate failed: $(echo "$out" | tail -5)"
  fi

  if ! out="$(python3 -m pytest -q "$SRC/test_render_role.py" 2>&1)"; then
    _vmiss "test suite fails: $(echo "$out" | tail -5)"
  fi

  vdone
fi

echo "nothing to install: $SRC/roles.yaml is config-only, no live hardware target yet (see README.md)"
