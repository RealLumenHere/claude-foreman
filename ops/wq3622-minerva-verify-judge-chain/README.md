# wq#3622 — verify-judge chain dead: cerebras-oss-120b (402) -> gemini-flash (20 RPD) -> fail-closed

## Root cause

`/home/lumen/agent/.env` (live, not readable by this worker — see "Constraint" below) sets:

```
VERIFY_JUDGE_MODEL=cerebras-oss-120b
VERIFY_JUDGE_MODEL_2=gemini-flash
CONTINUAL_LEARN_MODEL=cerebras-oss-120b
CONVO_LEARN_MODEL=cerebras-oss-120b
```

Every one of those rungs is dead:

- `cerebras-oss-120b`: 402 (billing lapsed) since 2026-08-19. The fleet's own Cerebras
  fallback edge already routes around it to `qwen36-local` (Isis llama-server, 8k ctx) —
  i.e. the fleet itself no longer trusts this rung for anything else, but the judge chain's
  env still names it as primary.
- `gemini-flash`: 20 RPD quota, exhausted daily (wq#3543: same host also hit a 403
  `PERMISSION_DENIED` from a malformed AI Studio key, tracked separately as wq#3525,
  Frank-gated on a physical key regen).
- Net effect in `minerva_verify.py:judge_entailment_uq()`: primary 4xxs/402s on every
  sample, falls through to `JUDGE_MODEL_2` (aq#66's fallback wiring), which also 4xxs once
  the daily quota is gone — `all_runs` stays empty, the guard fail-closes
  (`fail_closed:...+fallback:...`) with **zero working judge rungs for weeks**, not a
  transient blip.

## Decision (autonomous, per Instinct 3)

Repoint the chain to the two rungs that are actually alive today (2026-10-07):

- **Primary -> `deepseek-v4-flash`.** Sep-03 austerity had moved spend off deepseek while
  the balance was low; it is back as an interim rung via OpenRouter fallback (~$0.50/day,
  2026-10-07) and its `response_format: json_object` pass-through is already verified for
  this exact code path (`aq#66`, see `minerva_verify.py`'s `_judge_once`, the
  `'response_format': {'type': 'json_object'}` comment). This is a paid rung, but it is the
  ONLY alive+cheap option — the alternative is what's been happening for weeks: a judge
  that never answers.
- **Secondary -> `qwen36-local`.** Free/local (Isis llama-server), already proven as a judge
  rung by wq#1501/wq#1529. Keeping the FALLBACK leg free preserves Frank's no-paid-spend
  constraint for the degraded path: a primary outage costs nothing extra, it just gets
  slower (qwen36-local: ~10-11s warm / 44-150s cold per wq#1501's measurements).
- **`CONTINUAL_LEARN_MODEL` / `CONVO_LEARN_MODEL` -> `deepseek-v4-flash`.** Same dead
  `cerebras-oss-120b` default, no in-repo consumer found for either name (not in this git
  tree — they're read directly by out-of-repo agent code), so there is no code-layer fix to
  make for these two; they are included as a straight env repoint in
  `agent_env_fragment.txt` for consistency with the judge chain's primary rung.
- Fail-closed semantics are unchanged: `judge_entailment_uq()` still returns
  `fail_closed:...` with zero working samples. This ticket restores a judge that actually
  gets samples most of the time; it does not touch the all-rungs-down path.

## What's here

| File | Purpose |
|---|---|
| `minerva_verify.py` | Full file, based on the most recent in-repo mirror copy (`ops/wq2027-intel-proposal-source-grounding/`, 2026-09-28 — the newest available; this worker has no grant to read the live file or its hourly `research/minerva-ref` mirror directly, see Constraint). One change: `VERIFY_JUDGE_MODEL` code default `'cerebras-oss-120b'` -> `'deepseek-v4-flash'`; `VERIFY_JUDGE_MODEL_2` code default `'deepseek-v4-flash'` -> `'qwen36-local'` (lines 36/46-47 with the explanatory comment). Belt + suspenders with the env fragment below, same shape as wq#1529's reasoning: both layers agree, so a missing/reverted drop-in or blank env var still lands on a working rung instead of silently reverting to the dead one. |
| `agent_env_fragment.txt` | The explicit override for `/home/lumen/agent/.env` — 4 keys, upserted in place (never a wholesale file copy; wq#1617's lost-update lesson generalized from the litellm yaml case to env files). |
| `install.sh` | Grep-gated (not hash-gated — no read grant to compute a live pre-image sha256) patch + upsert + restart + `stale-env-audit --fail-on-drift` script. Root-only, run via `lumen-run start --root`. |
| `verify_judge_langfuse.py` | Re-runnable evidence: queries Langfuse's `GET /api/public/observations?type=GENERATION` (same endpoint-verified-live shape as `ops/wq1554-langfuse-lag-monitor/probe_live.py`, not a guessed endpoint) for a recent `model=deepseek-v4-flash` generation, proving a judge call actually reached LiteLLM and got logged. |

## Constraint: this worker cannot apply or verify this live

Per `harness/fleet-exec-policy.yaml`:
- `/home/lumen/agent` (live `.env` + `.env.bak` tokens) and `/home/lumen/minerva` (credentials
  mirrored into `context/sections`, wq#1467) are deliberately absent from every `allow.read_dirs`
  entry, including the sweep-gated hourly mirror at `/home/lumen/research/minerva-ref` — that
  mirror IS listed, but reaching it via `fleet-exec run` 402s with `grant_missing_env`: this
  ticket's dispatch carries no `needs:` tag for it, so no per-ticket grant env was issued
  (fleet-exec requires the per-ticket grant AND the static policy to agree).
- `allow.restart` lists only `isis:[ollama]` and `lumen:[lumen-messenger, lumen-pokemon-live]`
  — `minerva-proposal.service` is not restartable via `fleet-exec` at all today.
- `sudo` is v2 (no verb yet) and the worker-settings.json `Bash` allowlist denies `sudo *`
  outright and only allows `systemctl restart lumen-messenger` specifically.

So, same as wq#3543/wq#897/wq#948/wq#1295/wq#1406/wq#1590/wq#2627/wq#2471: this bundle is
staged, not applied. `install.sh` must be hand-run (`lumen-run start --root`) by whoever has
the mirror-read + restart grant this worker doesn't, then `verify_judge_langfuse.py` run to
confirm the Langfuse evidence this ticket asks for.

## Rollback

Restore `$BACKUP_DIR/<stamp>.minerva_verify.py.bak` over `/home/lumen/minerva/minerva_verify.py`
and `$BACKUP_DIR/<stamp>.agent.env.bak` over `/home/lumen/agent/.env`
(`BACKUP_DIR=/home/lumen/backups/wq3622`), then `systemctl restart minerva-proposal.service`.
