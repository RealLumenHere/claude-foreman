#!/usr/bin/env bash
# wq#4149 OpenHands 1.8 hardening — RUN-IN-PLACE tools. Nothing here is copied to
# /home/lumen/bin or /etc; harden_app.py and reaper.py are invoked directly from this
# path by a privileged (docker-capable) lane, per README.md's "What the privileged
# lane runs next".
#
# --verify (ops/VERIFY_CONTRACT.md) checks the two things that would make this package
# merged-but-not-live:
#   1. harden_app.py's hardcoded targets (COMPOSE_DIR, WORKSPACE_DIR) still exist on
#      this host. If either moved, the script is dead code nobody would notice until a
#      privileged lane tried to run it for real.
#   2. this ticket's item 7 ("closed this ticket" per research/OPENHANDS_1_8_READINESS.md
#      §4) — check_openhands_alive() in the sibling wq4111 bundle's probe.py — still
#      exists and still answers a true liveness check against the running container.
#      Read-only GET, no docker, no sudo.
#
# From a sandboxed worker (bare `bash <abs path>` is not on the allowlist, ISSUE-LOG #770):
#   /home/lumen/bin/lumen-run start -- bash /home/lumen/foreman/ops/wq4149-openhands-1-8-hardening/install.sh --verify
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PROBE_DIR="$HERE/../wq4111-openhands-litellm-production"
PROBE="$PROBE_DIR/probe.py"
COMPOSE_DIR=/home/lumen/containers/openhands
WORKSPACE_DIR=/home/lumen/.openhands

if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"

  [ -d "$COMPOSE_DIR" ] || _vmiss "absent: $COMPOSE_DIR (harden_app.py's COMPOSE_DIR)"
  [ -e "$WORKSPACE_DIR" ] || _vmiss "absent: $WORKSPACE_DIR (harden_app.py's WORKSPACE_DIR)"

  vcontains "$PROBE" "def check_openhands_alive"

  if [ -r "$PROBE" ]; then
    python3 -c "import sys; sys.path.insert(0, '$PROBE_DIR'); import probe; sys.exit(0 if probe.check_openhands_alive() else 1)" \
      || _vmiss "openhands /alive not answering 200 {status: ok} via $PROBE"
  fi

  vdone
fi

echo "nothing to install: this bundle runs in place."
echo "  python3 $HERE/harden_app.py --dry-run"
echo "  python3 $HERE/reaper.py   # import only — see module docstring for wiring"
python3 "$HERE/test_reaper.py" -v
echo "liveness of the host-side readiness probe this package depends on: python3 $PROBE"
