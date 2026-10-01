#!/usr/bin/env bash
# wq#3282 canary, run ON thoth with sudo. Stops sonarr, waits for the real timer to bring it back.
# Restores sonarr itself if the timer does not (service restoration invariant).
set -u
U=${1:-sonarr}
[ "$(systemctl is-active wg-quick@wg0)" = active ] || { echo "SKIP wg0 not active"; exit 2; }
[ "$(systemctl is-active "$U")" = active ] || { echo "SKIP $U not active to begin with"; exit 2; }
echo "stopping $U at $(date -u +%T)"; systemctl stop "$U"
for i in $(seq 1 40); do          # 40 x 10s = 400s, one 5 min tick plus slack
  sleep 10
  if [ "$(systemctl is-active "$U")" = active ]; then echo "PASS $U returned after ~$((i*10))s"; exit 0; fi
done
echo "FAIL $U still down after 400s, restoring by hand"; systemctl start "$U"; exit 1
