#!/usr/bin/env bash
# wq#4629 — oneshot drain, woken by commander-job-notify.path (systemd.path,
# inotify under the hood) whenever lumen-job-notify.sh drops a marker into
# the done dir. For each marker: ntfy-send the result, optionally spawn a
# fresh `claude -p` session seeded with it (the only real way to "inject" an
# async result into Claude -- nothing can push into an already-running turn),
# then delete the marker so the directory empties and the path unit re-arms.
set -euo pipefail

done_dir="${COMMANDER_WATCHDOG_DONE_DIR:-/home/lumen/var/commander-watchdog/done}"
ntfy_send="${NTFY_SEND_BIN:-/home/lumen/bin/ntfy-send}"
claude_bin="${CLAUDE_BIN:-claude}"
notify_claude="${COMMANDER_WATCHDOG_NOTIFY_CLAUDE:-0}"

[ -d "$done_dir" ] || exit 0

for marker in "$done_dir"/*.json; do
  [ -e "$marker" ] || continue
  unit=$(jq -r '.unit' "$marker")
  result=$(jq -r '.result' "$marker")
  finished_at=$(jq -r '.finished_at' "$marker")
  msg="lumen job ${unit}: ${result} (finished ${finished_at})"

  "$ntfy_send" -T "Commander job finished" "$msg" || true

  if [ "$notify_claude" = "1" ]; then
    "$claude_bin" -p "$msg See ${marker} for the full marker." >/dev/null 2>&1 &
  fi

  rm -f "$marker"
done
