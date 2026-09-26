#!/usr/bin/env bash
# wq#2806: is the mask-secrets filter on the supergateway log pipe (wq#2546) running
# the program that is on disk?
#
# The pipe is `sed -E "$_PROG"` x6, and sed reads its program ONCE, at exec. Every
# rule added to /home/lumen/bin/mask-secrets after desktop-commander-mcp last started
# is on disk and NOT on the pipe: on 2026-09-26 the pipe (started 02:29Z) had no
# url-auth rule while the file (06:14Z) did. Same class as wq#2327 — a table loaded at
# spawn scores for the process's whole life — and nothing said so.
#
# Exit 0 CURRENT, 1 STALE, 2 no pipe found (the drop-in is not in effect, or the unit
# is down). Prints one line. A timer can turn 1/2 into an aq row; --verify runs it.
#
# The fix for STALE is a DETACHED restart of the unit, never a foreground one — the
# Commander session you are in rides the same process (HANDOFF §4):
#   sudo systemd-run --on-active=5 --unit=dc-mask-reload systemctl restart desktop-commander-mcp
set -u
SCRIPT=/home/lumen/bin/mask-secrets
UNIT=desktop-commander-mcp

main_pid=$(systemctl show -p MainPID --value "$UNIT" 2>/dev/null || echo 0)
[ "${main_pid:-0}" -gt 0 ] || { echo "log-pipe: NO PIPE ($UNIT not running)"; exit 2; }
# the filter is bash mask-secrets, child of the gateway, with sed children
filter_pid=$(pgrep -P "$main_pid" -f "$SCRIPT" | head -1)
[ -n "$filter_pid" ] || { echo "log-pipe: NO PIPE (no $SCRIPT child of $UNIT pid $main_pid — wq2546 drop-in not in effect?)"; exit 2; }
sed_pid=$(pgrep -P "$filter_pid" -x sed | head -1)
[ -n "$sed_pid" ] || { echo "log-pipe: NO PIPE (filter $filter_pid has no sed child)"; exit 2; }

# the running program is argv[2] of `sed -E <prog>`; the on-disk one is what the
# script would pass right now. Compare hashes, print nothing else (the program text
# holds nothing secret, but keep the line short).
running=$(awk 'BEGIN{RS="\0"} NR==3{printf "%s",$0}' "/proc/$sed_pid/cmdline" | sha256sum | cut -c1-12)
ondisk=$(bash -c 'source <(sed -n "/^_PROG=/,/^'"'"'$/p" '"$SCRIPT"'); printf "%s" "$_PROG"' | sha256sum | cut -c1-12)
started=$(ps -o lstart= -p "$sed_pid")
if [ "$running" = "$ondisk" ]; then
  echo "log-pipe: CURRENT (sed $sed_pid since $started, program $running)"
  exit 0
fi
echo "log-pipe: STALE (sed $sed_pid since $started runs $running, disk is $ondisk — rules edited after the unit started are not on the pipe; detached restart needed)"
exit 1
