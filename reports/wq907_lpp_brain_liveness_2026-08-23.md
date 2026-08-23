# wq#907 — LPP brain could block forever: bounded ladder, terminal fallback, slot breaker

Lumen Plays Pokemon stood still for 12h on 2026-08-23, process alive at 0.0% CPU with an open
socket to the gateway on `127.0.0.1:4000`. Nothing crashed and nothing alerted, because nothing
in the stack had a deadline.

## Root cause

Four layers each behaved reasonably and their product was unbounded.

| layer | what it did | bound |
|---|---|---|
| `src/brain.ts` `fetch()` | no `AbortSignal`, no dispatcher timeout | none |
| harness retry loop | `CFG.retries = 3` against the SAME model, 1.5s/3s/4.5s backoff | 3x the above |
| LiteLLM router | `num_retries: 3`, `retry_after: 2` on the model group | 3x `timeout` |
| `qwen36-local` deployment | `litellm_params.timeout: 600` | 600s per try |

The terminal rung is where it dead-ends: `qwen36-local` has **no entry in
`router_settings.fallbacks`** (the nephthys fallback was removed in wq#589 when that box was
decommissioned, and nothing replaced it), so an exhausted call ends at

    No fallback model group found for original model_group=qwen36-local. LiteLLM Retried: 3 times

after up to 30 minutes of waiting — an answer of "nothing", expensively.

### Correction to the ticket's chain

The ticket has the primary as `cerebras-oss-120b` falling back to `qwen36-local`. It does not.
`/etc/systemd/system/lumen-pokemon-live.service.d/30-brain-env.conf` sets
`Environment=LPP_BRAIN_PRIMARY=qwen36-local` (wq#722, cheapest-first), and
`/home/lumen/pokemon/fallback_stats.jsonl` agrees — 63 of 64 entries carry
`"primaryModel":"qwen36-local"`, none carry cerebras. So **every ordinary LPP turn** went
straight to the single-slot Isis rung with a 600s timeout and no fallback group, which makes the
exposure larger than the ticket describes, not smaller.

Isis (`100.100.129.24:11434`) serves that model with exactly one decode slot and cannot be given
more — the qwen35moe architecture blocks parallel slots regardless of `OLLAMA_NUM_PARALLEL`
(wq#898, ruled out there with evidence). So a second caller does not fail, it *queues*, and pays
the full 600s.

## Fix

Three layers, and the load-bearing one is deliberately the client.

### 1. `src/brain.ts` — a rung ladder that cannot dead-end

lumen-plays-pokemon PR [#74](https://github.com/frankstupak/lumen-plays-pokemon/pull/74).

`Brain.complete()` no longer retries one model into the void. It walks an ordered ladder:

    <profile's own model>  deadline LPP_BRAIN_TIMEOUT_MS           (default 120s)
    cerebras-oss-120b      deadline LPP_BRAIN_FALLBACK_TIMEOUT_MS  (default 30s)
    mistral-small          deadline LPP_BRAIN_FALLBACK_TIMEOUT_MS, 2 attempts

- Every request carries `AbortSignal.timeout(rung.timeoutMs)`, covering connect, headers and
  body. No gateway behaviour can make a turn outlive its deadline. This is the only layer that
  cannot itself dead-end, which is why it is here and not only in the proxy config.
- The terminal rungs are on **two different cloud providers**. A chain that ends on one failure
  domain is still a dead end.
- Fallback rungs get a *short* deadline. A fallback that can take minutes is the original bug
  one rung down — the ticket's item 2, applied per path rather than globally.
- The ladder replaces `CFG.retries`: non-terminal rungs get one attempt, because retrying a hung
  rung is exactly what cost 12 hours. Only the terminal rung retries.

### 2. Circuit breaker on the saturated rung (ticket item 3)

LPP cannot see Isis's decode slot, and aborting does not free it — LiteLLM keeps generating for
the rest of *its* timeout after the client disconnects. So after
`LPP_BRAIN_BREAKER_FAILS` (2) consecutive failures a rung is presumed busy and skipped outright
for `LPP_BRAIN_BREAKER_COOLDOWN_MS` (10 min). A saturated Isis goes from "120s wasted every turn"
to "120s wasted twice per 10 minutes". Cheapest-first resumes by itself when the cooldown
expires — no restart, no operator.

The terminal rung is **never** skipped by the breaker, so every turn makes at least one real
request. "All breakers open, so no request was sent" would be the same standing-still bug in a
new costume.

### 3. `/home/lumen/agent/litellm_config.yaml` — gateway is no longer a terminal node

Backed up to `litellm_config.yaml.bak.20260823_pre_wq907`.

- Added `fallbacks` groups for `qwen36-local` and the new `qwen36-live`, both
  `[cerebras-oss-120b, mistral-small]`. The gateway can no longer answer "no fallback model
  group found".
- Added `qwen36-live` — same Isis model and api_base, `timeout: 120` instead of 600. Split out
  rather than lowering the shared alias because the two callers want opposite things from the
  same box: foreman's `mechanical-edit` rung can afford ten minutes, a live game turn cannot
  afford one. `qwen36-local` keeps 600s for batch callers, with the reason written next to it.

## Verification

`src/accept_brain907.ts` — real emulator, real `Agent.runTurn()`, real `Brain`, real gateway,
driven through a proxy that blackholes one model by name. Blackhole, not connection-refused: the
socket was open the whole 12h, so a refused-connection test would have gone green while the
actual failure mode walked past it. All writes go to a scratch tree; live game state is read-only.
Run at production defaults (120s/30s), not test values.

    scratch=/tmp/wq907-accept-7vrmoh proxy=:36155 primary=qwen36-local
    deadline=120000ms fallback=30000ms rungs=cerebras-oss-120b,mistral-small

    -- PHASE A: primary rung accepts and never answers (the 12h bug) --
       turn 1: 121634ms err=none rungs=qwen36-local,cerebras-oss-120b
       turn 2: 121476ms err=none rungs=qwen36-local,cerebras-oss-120b
       turn 3:  14509ms err=none rungs=cerebras-oss-120b
       turn 4:   2500ms err=none rungs=cerebras-oss-120b

    -- PHASE B: primary rung killed outright (unknown model group) --
       turn:     1743ms err=none rungs=wq907-killed-model,cerebras-oss-120b

    -- PHASE C: every rung blackholed — the ladder must FAIL, bounded --
       turn:    91547ms err=Error: deadline 30000ms exceeded on mistral-small

    -- PHASE D: rungs restored — play resumes without a restart --
       turn:     1610ms err=none rungs=cerebras-oss-120b

    11 PASS / 0 FAIL

Read the `rungs=` column: it is the list of models that actually reached the proxy.

- **A1/A2** — the old code's 12h hang, now 121.6s: the deadline fires, the ladder rescues the
  turn on cerebras, the agent plays. Two failures open the breaker.
- **A3/A4** — `qwen36-local` is *absent* from `rungs`. No request is sent to the saturated slot
  at all, and a degraded turn costs 2.5s instead of 121s. This is the item-3 fix, measured.
- **B** — killing the primary outright degrades in 1.7s.
- **C** — test to failure. Every rung blackholed: the ladder throws, named and bounded
  (30s + 2x30s terminal + backoff), which is `live_agent.ts`'s survivable `failStreak` path
  rather than a hang.
- **D** — recovery needs no restart. `rungs` is still cerebras-only because the primary breaker's
  10-minute cooldown has not expired; cheapest-first returns on its own when it does.

Old behaviour for the same Phase A input: 600s gateway timeout x 3 router retries x 3 harness
retries, then `No fallback model group found`, per turn, forever.

Unit coverage, offline and deterministic, added to both probe suites
(`scripts/probes-pure.sh`, `scripts/probes-main.sh`): `src/probe_brain907.ts`, 19 assertions over
ladder shape, deadline enforcement, rescue, total blackout, breaker open/skip, terminal-rung
exemption, and healthy-primary preference.

`probe_vision621`'s token-accounting assertion matched one literal spelling of the `think_chunk`
emit in `brain.ts`; it now matches the emit *shape*, since the invariant is the shape.

## Not applied, on purpose

**The gateway is still running the pre-wq#907 config.** LiteLLM here is a container and the
config is read at startup; there is no `/config/update` route on this build (checked
`/openapi.json`), and `/model/new` needs `store_model_in_db`, which `general_settings` does not
set. Restarting it needs a `docker` call this session is not permitted to make. So section 3 is
committed to disk and takes effect at the next gateway restart. The YAML was validated
(`yaml.safe_load`, 34 models, zero dangling fallback targets) and every added block is
structurally identical to blocks already in the file.

This does not leave LPP exposed: section 1 is the bound that matters, it is live in the LPP repo
now, and it holds whatever the gateway does.

**`LPP_BRAIN_PRIMARY` was left on `qwen36-local`.** Pointing it at `qwen36-live` before the
gateway knows that alias would silently abandon cheapest-first until someone restarted the proxy.
Flipping it belongs in the same operation as the restart.

Both are wq#917 (ops), with the ordering written down: restart, verify `qwen36-live` answers,
*then* flip the drop-in.

**Per-deployment `num_retries: 0` on the Isis rungs was not added.** It would cut the
gateway-side `3 x timeout` before fallback, but it is unverifiable without a restart to test it
against, and an unverifiable edit to the fleet's shared proxy config is not worth the blast
radius. Noted in the follow-up.

## Also found

`probe_goto583` is RED on `origin/main` (5 fail / 2 pass, G4-G6) before any change in this
ticket — reproduced on the untouched primary tree. Unrelated to brain routing; not fixed here.

`cerebras-oss-120b` is currently failing at the provider and being covered by its own gateway
fallback to `deepseek-v4-flash` — a direct call to it returns 200 with
`"model":"deepseek-v4-flash"` in the body. The ladder's first cloud rung is therefore itself
degraded right now, which is precisely why the terminal rung is on a different provider.
