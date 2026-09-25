# wq#2499 KV-cache A/B kit (nephthys)

Measures f16 KV (current) against q8_0 KV + flash attention on nephthys's ollama.
Report: `research/KV_CACHE_Q8_NEPHTHYS.md`.

`kvbench.py` needs HTTP only. The apply/revert steps need a shell on nephthys,
which no build worker has: nephthys is absent from `fleet-exec-policy.yaml`'s `ssh:`
and `restart:` lists (ISSUE-LOG row for wq#2499). Run them from Lumen as an operator.

## Arm A (f16, done)

    python3 kvbench.py --host http://100.98.74.87:11434 --model qwen3:14b \
        --label f16-baseline --reps 3 --out f16.json
    ssh nephthys@100.98.74.87 'ps -C ollama -o pid=,rss=,args= | cut -c1-80'

## Apply (backup first, ollama restart only, never the host)

    ssh nephthys@100.98.74.87 'cd /etc/systemd/system/ollama.service.d && \
      sudo cp -p override.conf override.conf.bak.$(date +%Y%m%d_%H%M%S)_wq2499'
    scp 20-kvcache.conf nephthys@100.98.74.87:/tmp/
    ssh nephthys@100.98.74.87 'sudo install -m644 /tmp/20-kvcache.conf \
      /etc/systemd/system/ollama.service.d/ && sudo systemctl daemon-reload && \
      sudo systemctl restart ollama && systemctl show ollama -p Environment'

Confirm the flag took: `journalctl -u ollama` on the next load should show
`flash_attn = 1` / `type_k = 'q8_0'`; a bare f16 line means FA was refused and
V fell back.

## Arm B

    python3 kvbench.py --host http://100.98.74.87:11434 --model qwen3:14b \
        --label q8_0-fa --reps 3 --out q8.json
    ssh nephthys@100.98.74.87 'ps -C ollama -o pid=,rss=,args= | cut -c1-80'

## Revert (if rejected)

    ssh nephthys@100.98.74.87 'sudo rm /etc/systemd/system/ollama.service.d/20-kvcache.conf && \
      sudo systemctl daemon-reload && sudo systemctl restart ollama'

Then confirm serving: `curl -s http://100.98.74.87:11434/api/ps` and one
local-worker completion through LiteLLM.
