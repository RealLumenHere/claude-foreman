#!/usr/bin/env bash
# wq#1747/#1968 LiteLLM key rotation — a RUN-IN-PLACE tool. Nothing here is copied to
# /home/lumen/bin or /etc; `rotate.sh` is invoked from this directory:
#   python3 /home/lumen/foreman/ops/wq1747-litellm-key-rotation/rotate.py --help
#
# It still needs a --verify handler (ops/VERIFY_CONTRACT.md), because since wq#3224 the
# rotation is no longer self-contained: confirm_no_drift() shells out to
# `/home/lumen/bin/stale-env-audit --fail-on-drift ... --vars ...` to decide whether the
# consumer restarts actually took, and that binary lives in a different bundle. wq#3228
# found the gap this closes — rotate.py gating on two flags the installed build did not
# implement, which the build silently ignored and answered 0 to. Merged was not live, and a
# repo-only check could not tell the difference.
#
# From a sandboxed worker (bare `bash <abs path>` is not on the allowlist, ISSUE-LOG #770):
#   /home/lumen/bin/lumen-run start -- bash /home/lumen/foreman/ops/wq1747-litellm-key-rotation/install.sh --verify
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  # The gate rotate.py calls must exist in the installed binary, by its own --help.
  vflag /home/lumen/bin/stale-env-audit --fail-on-drift
  vflag /home/lumen/bin/stale-env-audit --vars
  # ...and rotate.py in the tree that actually runs must still be the gated version.
  vcontains "$HERE/rotate.py" "confirm_no_drift"
  vcontains "$HERE/rotate.py" "--fail-on-drift"
  vdone
fi

echo "nothing to install: this bundle runs in place."
echo "  python3 $HERE/rotate.py --help"
python3 -m pytest -q "$HERE/test_rotate.py"
echo "liveness of the drift gate: bash $0 --verify"
