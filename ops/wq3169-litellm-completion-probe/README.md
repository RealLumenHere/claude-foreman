# wq#3169 — LiteLLM completion probe

`lumen-model-check` only checks that a model ID appears in `/v1/models` — it
never sends a completion. That misses a dead api_base host, an expired
upstream key, or an exhausted quota; those stayed invisible until a real
request hit the failing rung. This productionizes the one-shot audit script
at `/home/lumen/scratch/audit_20260929/probe2.py` into a recurring probe.

## Components

- `litellm-completion-probe` — sends one real chat completion (`max_tokens
  300`, 60s timeout, parallel via a 6-worker pool) per distinct `api_base`
  host in `litellm_config.yaml` (one rung per host — a dead host fails every
  model on it identically, so probing more just burns quota). Embed/
  rerank/whisper/tts deployments are skipped. Master key is read from
  `agent/.env` by regex, used only in the `Authorization` header, never
  printed or logged.
- Edge-triggered alerting, same contract as `litellm-auth-alert` (wq#911)
  and `health_check_failure_alert.py` (wq#1295): `aq add` + urgent ntfy on
  clear→breach, aq bump (no re-ntfy) while the breach persists, low-pri
  ntfy on breach→clear. aq dedup key: `(litellm-completion, <host>)`.
- `litellm-completion-probe.service` / `.timer` — oneshot + timer, every 30
  min (`OnBootSec=5min OnUnitActiveSec=30min`). `ExecStartPost` calls
  `lumen-cron-stamp %n` so a stopped timer surfaces via
  `lumen-cron-deadman` instead of going silently stale.
- `install.sh` — idempotent deploy (script + units + `enable --now`).
- `tests/` — unit tests for host grouping, master-key extraction (never
  printed), and the edge-trigger state machine. No network calls.

## Deploy status

Deployed 2026-09-29 via
`lumen-run start -- bash ops/wq3169-litellm-completion-probe/install.sh`
(installing units needs root; a build worker cannot call `sudo` directly).
`litellm-completion-probe.timer` is `enabled`/`active`.

## Verified live (2026-09-29)

```
$ litellm-completion-probe --print
probed 13 hosts, 4 failing: anthropic,100.98.74.87:11434,host.docker.internal:8000,host.docker.internal:8091
```

Matches the raw `probe2.py` audit run exactly (same 4 hosts: no Anthropic
key set, `100.98.74.87` nephthys worker down, `host.docker.internal:8000`/
`:8091` local model servers down). `aq list open` shows 4 new
`litellm-completion` rows (ids 2579/2581/2583/2585), and
`/home/lumen/agents/litellm-completion-probe/state.json` records
`failed: true` for exactly those 4 hosts.

## Docs correction (second half of this ticket)

Live `litellm_config.yaml` has 0 SambaNova mentions and 1 Mistral mention
(an unused `MISTRAL_API_KEY: MISTRAL_API_KEY` env mapping — no model_list
entry uses it). `research/DSH_EVAL.md:61` claimed researchers ran on
"Cerebras / SambaNova / Mistral / OpenRouter" free tiers; corrected to
reflect that SambaNova was fully retired fleet-wide in wq#381
(`research/VENDOR_KEY_ROTATION.md:44`), and that `lumen-fleet-mistral.service`
still runs but has routed to `deepseek-v4-flash` since wq#2903/wq#2927, not
the Mistral API (`agent/lumen_fleet_researcher.py:52`).
