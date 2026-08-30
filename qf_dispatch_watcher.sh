#!/bin/bash
# questfree-app overnight dependency dispatcher.
# Dispatches 689 (needs 688), 691 (needs 688,690,689), 692 (needs 688,690) as deps close.
# Idempotent: only dispatches a ticket once, only when all deps are 'done'/'verified'.
export HOME=/home/lumen
export PATH=/home/lumen/.npm-global/bin:/usr/local/bin:/usr/bin:/bin
export WQ_AGENT=orch-uni-a5f1c4
CB=/home/lumen/.npm-global/bin/claude
WQ=/home/lumen/bin/wq
REPO=/home/lumen/questfree
LOG=/home/lumen/foreman/_runs/qf_watcher.log
declare -A DEPS=( [689]="688" [691]="688 690 689" [692]="688 690" )
dispatched_file=/home/lumen/foreman/_runs/qf_dispatched.txt
touch "$dispatched_file"
status_of(){ $WQ show "$1" 2>/dev/null | awk -F: '/^status/{gsub(/ /,"",$2);print $2}'; }
echo "[$(date -u +%H:%M:%S)] watcher start" >> "$LOG"
for i in $(seq 1 240); do   # ~4h at 60s
  for T in 689 691 692; do
    grep -q "^$T\$" "$dispatched_file" && continue
    st=$(status_of "$T"); [ "$st" = "claimed" ] && { echo "$T" >> "$dispatched_file"; continue; }
    ok=1
    for d in ${DEPS[$T]}; do
      ds=$(status_of "$d"); [ "$ds" = "done" ] || [ "$ds" = "verified" ] || ok=0
    done
    if [ "$ok" = "1" ]; then
      echo "[$(date -u +%H:%M:%S)] deps met for $T -> dispatch" >> "$LOG"
      lumen-run start --name fworker-wq$T --timeout-stop-sec 90 --mem 8G --mem-high 6G --cpu 400% --tasks-max 512 --oom-policy stop -- \
        python3 /home/lumen/foreman/harness/fworker.py run --ticket $T --repo $REPO \
        --model claude-sonnet-5 --effort medium --archetype standard-engineering --claude-bin $CB >> "$LOG" 2>&1
      echo "$T" >> "$dispatched_file"
    fi
  done
  # stop early if all three dispatched
  c=$(grep -cE "^(689|691|692)$" "$dispatched_file")
  [ "$c" -ge 3 ] && { echo "[$(date -u +%H:%M:%S)] all dependents dispatched, exit" >> "$LOG"; break; }
  sleep 60
done
echo "[$(date -u +%H:%M:%S)] watcher done" >> "$LOG"

# questfree-root lane additions
DEPS[694]="693"
DEPS[695]="694"
