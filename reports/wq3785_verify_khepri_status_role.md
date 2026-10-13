# wq#3785 verify-pass: khepri Mac Pro status and role

**Verdict: KEEP SERVING, as-is.** Live checks this session (2026-10-13) confirm khepri is
online, running Ollama, actively serving a model, and idling at modest power — not a
candidate for optimization or retirement right now.

## Access note

This ticket's `needs:` field is empty (`wq show 3785`), so `fleet-exec` denies
`grant_missing_env` for every `fleet-exec run khepri ...` attempt before argv is even
checked — the same gap logged at `kb/ISSUE-LOG.md` row 670 (wq#2509, 2026-09-24). Separately,
khepri is still absent from `fleet-exec-policy.yaml:189`'s `ssh:` allowlist even today, so an
ssh grant would not have helped either. All findings below come from direct HTTP calls over
the tailnet (`curl`, allowed on this worker's allowlist) — no ssh, no proxy.

## Live checks run this session

1. **Tailscale reachability**: `curl -m8 http://100.120.16.78:11434/api/tags` → HTTP 200.
   khepri's tailnet IP (`lumen_state.py current khepri` → `khepri.tailscale_ip =
   100.120.16.78`) answers directly — it is online and reachable now, not just per a stale
   recorded fact.
2. **Ollama running + serving a model**: the `/api/tags` body lists one model,
   `qwen2.5-coder:7b`, `modified_at: 2026-10-07T07:09:24Z` — matches
   `lumen_state.py current khepri`'s `khepri.ollama_version = 0.40.0 (since 2026-10-07)`
   exactly, so the recorded fact is current, not stale.
3. **Model actively loaded, not just installed**: `curl -m8
   http://100.120.16.78:11434/api/ps` shows `qwen2.5-coder:7b` resident with `size_vram:
   4109212055` (~4.1GB) and `expires_at: 2026-10-13T09:21:37Z` — about 1h51m past the probe
   time (`date -u` → `2026-10-13T07:30:29Z`). The model is being kept warm in VRAM well past
   Ollama's 5-minute default keep-alive, i.e. khepri is configured to serve requests with no
   cold-load penalty, not just hosting a dormant binary.
4. **Idle power / load, not actively crunching right now**: netdata on khepri's own tailnet
   IP (`http://100.120.16.78:19999`):
   - `system.load` → `load1: 0.015` — essentially idle.
   - `system.cpu` (last 60s) → `user 0.29% + system 0.36%` — idle.
   - `cpu.powercap_intel_rapl_zone_package-0` → `18.16W` CPU package power. (No GPU power
     sensor exposed; `sensors.temperature_amdgpu-pci-0200_temp1_edge_input` reads `44.0C`,
     consistent with idle — the wq#2166 soak baseline put loaded-GPU die temp at 66.8C.)
   18W CPU package power while holding a model warm in VRAM is not a "burning electricity
   idle" node — this is a lean standby cost for an inference node waiting on requests, not
   waste.
5. **Hardware record cross-check**: `research/fleet_hardware.json:82-91` (Mac Pro 6,1, Xeon
   E5-2697 v2 12C/24T, 64GB DDR3 ECC, 2x FirePro D500/Tahiti LE, 256GB blade) matches
   `lumen_state.py current khepri`'s `khepri.hardware` fact verbatim — no drift between the
   static inventory and the live-verified record.

## One unverified claim, flagged not fixed

`lumen_state.py current khepri`'s `khepri.role` and `fleet_hardware.json:91` both say khepri
is wired into LiteLLM as the "qwen-local rung." Grepping the actual LiteLLM config sources in
this repo (`ops/inference-registry/litellm_local_block.yaml`,
`ops/wq3212-litellm-dead-rungs/`) turns up no `khepri` or `qwen-local` entry — only
`nephthys-qwen14b`/`qwen36-local` pointing at nephthys. `docker ps`/`docker logs` to check the
live LiteLLM container's loaded config directly were denied by this session's permissions.
So: khepri's Ollama API itself is confirmed live and serving, but whether the fleet's router
is actually sending it traffic (vs. the API sitting reachable but unrouted) is **not**
independently confirmed here — worth a follow-up ticket with `needs:` set appropriately to
inspect the live LiteLLM config.

## Decision

**Keep serving.** khepri is online, Ollama is healthy and current (0.40.0), it holds a real
model warm and ready (`qwen2.5-coder:7b`), and its idle draw (18W CPU package, near-zero
load) is the expected standby cost for a kept-warm local inference node, not idle waste. No
retirement or hardware-level optimization is warranted from this pass. The one open thread —
confirming khepri actually receives LiteLLM-routed traffic rather than just answering direct
probes — should be a separate, narrowly-scoped follow-up ticket (filed with
`needs:ssh(khepri)` or docker access so the worker isn't blocked the way this one would have
been).
