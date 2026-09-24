#!/bin/bash
# wq#2447 v2 on nephthys (v1 was cut short by a power loss at 18:33Z, see REPORT). Same prompts/params. Adds guards: only start a config when
# ollama has no client and load<1; abort+retry a config if any client hits production ollama mid-run. localhost only, no ufw change.
D=/home/nephthys/pilots/wq2447; BLOB=/usr/share/ollama/.ollama/models/blobs/sha256-a8cc1361f3145dc01f6d77c6c82c9116b9ffe3c97b34716fe20418455876c40e
export LD_LIBRARY_PATH=/usr/local/lib/ollama; PORT=18447; cd $D
conn() { ss -tn state established '( sport = :11434 )' | tail -n +2 | wc -l; }
idle() { for i in $(seq 1 120); do c=$(conn); l=$(cut -d' ' -f1 /proc/loadavg); if [ "$c" = 0 ] && awk "BEGIN{exit !($l<1.0)}"; then return 0; fi; sleep 10; done; return 1; }
for cfg in none ngram-simple ngram-map-k ngram-mod none2; do
  flag=""; [ $cfg != none ] && [ $cfg != none2 ] && flag="--spec-type $cfg"
  for try in 1 2 3; do
    idle || { echo "=== $cfg no idle window, ABORT"; break; }
    rm -f abort.flag; rm -f res_$cfg.jsonl
    echo "=== $cfg try$try start $(date -u +%T) load=$(cut -d' ' -f1 /proc/loadavg)"
    /usr/local/lib/ollama/llama-server -m $BLOB -c 4096 -np 1 -t 12 --host 127.0.0.1 --port $PORT --jinja $flag > srv_$cfg.log 2>&1 &
    SP=$!
    for i in $(seq 1 120); do curl -sf localhost:$PORT/health >/dev/null && break; sleep 2; done
    ( while [ ! -f done.flag ]; do c=$(conn); echo "$(date -u +%T) $cfg conn=$c load=$(cut -d' ' -f1 /proc/loadavg)" >> conn.log; if [ "$c" != 0 ]; then touch abort.flag; kill $SP 2>/dev/null; fi; sleep 5; done ) &
    LG=$!
    rm -f done.flag; python3 bench.py $PORT res_$cfg.jsonl
    touch done.flag; sleep 6; kill $LG 2>/dev/null; kill $SP 2>/dev/null; wait $SP 2>/dev/null; rm -f done.flag; sleep 3
    if [ -f abort.flag ]; then echo "=== $cfg try$try INTERRUPTED by ollama client"; continue; fi
    echo "=== $cfg end $(date -u +%T) load=$(cut -d' ' -f1 /proc/loadavg)"; break
  done
done
echo ALLDONE
