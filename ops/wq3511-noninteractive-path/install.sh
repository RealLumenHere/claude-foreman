#!/usr/bin/env bash
# wq#3511: put /home/lumen/bin on PATH for NON-INTERACTIVE ssh sessions
# (Commander start_process, fworker, any `ssh lumen@lumen '<cmd>'`).
#
# Mechanism (verified, not assumed — probe results in README.md): this box's
# bash (5.3.9, Debian SSH_SOURCE_BASHRC patch) sources ~/.bashrc for a
# non-interactive shell launched with SSH_CLIENT set. The existing ~/.bashrc
# returns at the `case $- in *i*` guard (line 6) before any PATH export, and
# none of its PATH lines add /home/lumen/bin anyway (that lives in ~/.profile,
# login shells only). So the single place every non-interactive ssh session
# already reads is the TOP of ~/.bashrc, above the guard — this bundle inserts
# a marker-fenced block there (bashrc-block.sh).
#
# Run as lumen (NOT --root; ~/.bashrc is lumen's):
#   lumen-run start --name wq3511-path -- bash /home/lumen/foreman/ops/wq3511-noninteractive-path/install.sh
# Rollback: restore the printed ~/.bashrc.bak-wq3511-* backup, or delete the
# lines between the >>> wq3511 and <<< wq3511 markers.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BASHRC=/home/lumen/.bashrc
MARKER='>>> wq3511 fleet PATH for non-interactive ssh'
TOOLS="unit-drift lumen-run mask-secrets"
# Baseline PATH mirrors what sshd hands a non-interactive command (no
# /home/lumen/bin). env -i wipes the caller's PATH so an already-good caller
# environment cannot make this probe pass vacuously (wq#2630: a probe must be
# able to fail).
BASE_PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Emulates the exact code path sshd triggers: non-interactive bash, -c,
# SSH_CLIENT set -> Debian bash sources ~/.bashrc.
probe_emulated() {
  env -i HOME=/home/lumen PATH="$BASE_PATH" SSH_CLIENT='100.64.0.1 1 22' \
    bash -c "command -v $TOOLS" >/dev/null 2>&1
}

# wq#1696 deploy-verify contract. Read-only.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vcontains "$BASHRC" "$MARKER"
  probe_emulated || _vmiss "non-interactive ssh-shaped bash cannot resolve: $TOOLS"
  vdone
fi

if grep -qF "$MARKER" "$BASHRC"; then
  echo "already installed: marker present in $BASHRC"
else
  BAK="$BASHRC.bak-wq3511-$(date +%Y%m%d%H%M%S)"
  cp -p "$BASHRC" "$BAK"
  echo "backup: $BAK"
  CAND=$(mktemp /home/lumen/.bashrc.wq3511.XXXXXX)
  cat "$HERE/bashrc-block.sh" "$BASHRC" > "$CAND"
  bash -n "$CAND"   # never swap in a file bash cannot parse
  chmod --reference="$BASHRC" "$CAND"
  mv "$CAND" "$BASHRC"
  echo "installed block at top of $BASHRC"
fi

echo "--- verify: emulated non-interactive ssh shell ---"
if probe_emulated; then
  echo "EMULATED OK: $TOOLS resolve"
  EMU=0
else
  echo "EMULATED FAIL: $TOOLS do not resolve"
  EMU=1
fi

echo "--- verify: real non-interactive ssh (self, the Commander path) ---"
if ssh -o BatchMode=yes -o ConnectTimeout=8 lumen@100.117.66.102 \
     "command -v $TOOLS && echo WQ3511_SSH_OK"; then
  echo "REAL SSH OK"
else
  echo "REAL SSH probe could not run (no self-ssh key or host unreachable)" >&2
fi

exit "$EMU"
