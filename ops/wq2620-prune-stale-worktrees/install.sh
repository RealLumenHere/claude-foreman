#!/usr/bin/env bash
# Install wq#2620's stale-worktree pruner timer. Idempotent. Run from a root/
# interactive session on Lumen — a BUILD worker's sandbox has no sudo and cannot
# install, enable or start a systemd unit (see README.md "What a worker can and
# cannot do").
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  vunit_file "$HERE/lumen-prune-worktrees.service" /etc/systemd/system/lumen-prune-worktrees.service
  vunit_file "$HERE/lumen-prune-worktrees.timer" /etc/systemd/system/lumen-prune-worktrees.timer
  venabled lumen-prune-worktrees.timer
  vactive lumen-prune-worktrees.timer
  vdone
fi

# Dry run first, so the install lands against a current picture.
python3 "$HERE/prune_worktrees.py" || true
echo

sudo install -m 0644 "$HERE/lumen-prune-worktrees.service" /etc/systemd/system/lumen-prune-worktrees.service
sudo install -m 0644 "$HERE/lumen-prune-worktrees.timer" /etc/systemd/system/lumen-prune-worktrees.timer
sudo systemctl daemon-reload
sudo systemctl enable --now lumen-prune-worktrees.timer
systemctl list-timers lumen-prune-worktrees.timer --no-pager --no-legend
