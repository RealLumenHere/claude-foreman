#!/bin/bash
# wq#2447 on nephthys. baseline FIRST, then 3 self-speculative (n-gram, no draft model) configs, then baseline repeat for drift. localhost only, no ufw change.
D=/home/nephthys/pilots/wq2447; BLOB=/usr/share/ollama/.ollama/models/blobs/sha256-a8cc1361f3145dc01f6d77c6c82c9116b9ffe3c97b34716fe20418455876c40e
export LD_LIBRARY_PATH=/usr/local/lib/ollama; PORT=18447; cd $D
for cfg in none ngram-simple ngram-map-k ngram-mod none2; do
  flag=""; [ $cfg != none ] && [ $cfg != none2 ] && flag="--spec-type $cfg"
  echo "=== $cfg start $(date -u +%T) load=$(cut -d' ' -f1 /proc/loadavg)"
  /usr/local/lib/ollama/llama-server -m $BLOB -c 4096 -np 1 -t 12 --host 127.0.0.1 --port $PORT --jinja $flag > srv_$cfg.log 2>&1 &
  SP=$!
  for i in $(seq 1 120); do curl -sf localhost:$PORT/health >/dev/null && break; sleep 2; done
  python3 bench.py $PORT res_$cfg.jsonl
  kill $SP; wait $SP 2>/dev/null; sleep 3
  echo "=== $cfg end $(date -u +%T) load=$(cut -d' ' -f1 /proc/loadavg)"
done
echo ALLDONE
