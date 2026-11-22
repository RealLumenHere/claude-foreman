# wq#1406 — guard against silent fallback to PAID models

The DeepSeek/LPP runaway (Sep 1-2 2026): Cerebras 402'd, LPP's fallback
silently routed to `deepseek-v4-flash`, $23/day burned for two days with no
cap and no alert (LPP's own client-side fallback is already fixed to
local-only, wq#722/wq#907 — see `foreman/reports/wq907_lpp_brain_liveness_2026-08-23.md`).
This is the SYSTEMIC fix at the gateway layer, so the next instance of this
class — any caller, any chain — can't repeat it silently.

## Status: RETIRED, and `--verify` checks (wq#3486, wq#5253)

wq#3486 retired this bundle. `install.sh` refuses to install it (exit 2); see the
header comment for why. `install.sh --verify` checks two things. The
paid-spend-tripwire units are absent from `/etc/systemd/system`. The unit files of
the two replacement timers, `lumen-budget-alert.timer` and
`lumen-deepseek-spend.timer`, are present. It does not check that these timers are
enabled or active, because the 2026-10-08 subtraction disabled both on purpose
(`/home/lumen/scratch/subtraction/timers-disable-phase2.txt` line 13,
`timers-disable-list.txt` line 35). Running `install.sh` as root does not change any
of this. Re-enabling the timers is a decision. Whoever makes it adds
`venabled`/`vactive`/`vtimer_scheduled` back to the verify block. Test:
`ops/tests/test_wq5253_retired_verify.py`.

## Components

| File | Deploy target | Purpose |
|---|---|---|
| `audit_fallback_chains.py` | run against the live proxy config | (1) flags any `router_settings.fallbacks` chain that reaches a paid model (deepseek/openrouter/mistral/anthropic/openai/cerebras) before a free/local one, or that has no free/local rung anywhere in the whole primary+fallback path |
| `paid_fallback_alert.py` | onto the LiteLLM proxy's PYTHONPATH, registered as a `success_callback` | (2) fires ntfy ("X is down, serving via paid Y") the moment any request is actually served off a paid fallback deployment |
| `paid_spend_tripwire.py` | `/home/lumen/foreman/ops/wq1406-paid-fallback-guard/`, run by the timer below | (3) pages if any paid model's spend-today (via LiteLLM's own `/spend/logs`) crosses a per-model daily $ threshold — the backstop if (2) is missed or silenced |
| `paid-spend-tripwire.service`/`.timer` | `/etc/systemd/system/` | runs the tripwire every 30min (checks intraday, not just at midnight) |
| `litellm_config_snippet.yaml` | merge into the live proxy `config.yaml` (outside git, see below) | registers `paid_fallback_alert.py`'s callback |
| `install.sh` | — | idempotent installer for the tripwire timer |
| `tests/` | — | unit tests for every pure decision function, run standalone |

## Why the live config isn't touched directly, and the audit wasn't run against the real file

Same convention as wq#897/wq#911/wq#948/wq#1299 (see their READMEs): this
BUILD worker's sandbox has filesystem access only to its worktree plus
`--add-dir` roots (`/home/lumen/bin`, `/home/lumen/kb`, `/home/lumen/pokemon`,
`/home/lumen/agents/work-queue`, `/home/lumen/mailbot`,
`/home/lumen/projects/nest`, `/home/lumen/foreman`) — **not**
`/home/lumen/agent`, which is where the live LiteLLM proxy config actually
lives (`/home/lumen/agent/litellm_config.yaml`, confirmed via
`foreman/reports/wq907_lpp_brain_liveness_2026-08-23.md` §3, which edited
that exact path and backed it up to
`litellm_config.yaml.bak.20260823_pre_wq907`). No `docker`/`systemctl` write
access either. So:

- `audit_fallback_chains.py` is verified against fixture configs in
  `tests/test_audit_fallback_chains.py` (including the exact wq#907 shape —
  `qwen36-local -> [cerebras-oss-120b, mistral-small]` — as the "clean"
  case, and the DeepSeek/LPP runaway shape as a caught violation), not the
  live file. **Action needed on first deploy:** run it for real —
  `python3 audit_fallback_chains.py /home/lumen/agent/litellm_config.yaml`
  — and fix any reported violation before moving on to (2)/(3).
- `paid_fallback_alert.py`'s callback wiring (exact `success_callback` key
  name, exact kwargs shape LiteLLM hands a custom logger) is VERIFY
  territory, same caveat wq#1299 flagged for its own callback — confirm
  against `litellm --version` and the proxy startup log, and force one real
  fallback (kill a primary deployment, or reuse wq#907's blackhole-proxy
  test rig) to confirm an alert actually fires end-to-end.
- `paid_spend_tripwire.py` needs the live `/spend/logs` endpoint and the
  master key at `/home/lumen/.secrets/litellm-master.env` (same file
  `litellm-budget-alert` already reads) — neither reachable from this
  sandbox. Tested against synthetic spend dicts in
  `tests/test_paid_spend_tripwire.py`.

## Design notes

- **Paid provider list**: `deepseek/`, `openrouter/`, `mistral/`,
  `anthropic/`, `openai/`, `cerebras/` (litellm_params.model prefix — this
  is what actually selects the billing path, not the deployment alias).
  The ticket names the first five; `cerebras/` is added because the
  triggering incident *was* Cerebras returning 402 (payment required) — a
  "free-tier" cloud provider that becomes a paid drain once quota is
  exhausted is exactly the failure mode this guards against, not something
  to trust as free.
- **Audit rule is two-part**, not "any paid model in a chain is bad":
  1. A chain whose entire primary+fallback path has *no* free/local rung
     at all (a paid primary with only paid fallbacks) — the true
     DeepSeek/LPP-runaway shape.
  2. A chain whose fallback list *does* contain a free/local rung, but a
     paid rung is ordered before it.
  A free/local primary falling through to an all-paid fallback list as a
  genuine last resort (wq#907's actual, accepted config) is **not**
  flagged — that's the correct "local first, paid only if local is
  entirely down" shape the ticket asks for, not a violation of it.
- **Alert is edge-triggered** (fire on breach, cool down 30min while it
  persists, matching wq#911's `litellm-auth-alert` contract) rather than
  once-per-request, so a primary down for hours doesn't spam — but the
  first alert always fires immediately, so "no silent multi-day drain" is
  still guaranteed: worst case is a 30-minute detection window, not days.
- **Tripwire pages, does not cap** — matches the ticket ("alert, not
  necessarily cap per Frank"). It never touches keys, budgets, or
  containers; reporting only, same posture as `litellm-auth-alert`.
- **Tripwire runs every 30min, not once daily**, so a runaway is caught
  within the day it happens, not discovered the next morning reading a
  bill — which is exactly how the Sep 1-2 incident went undetected for two
  days.

## Test procedure (zero quota, no live gateway or ntfy required)

```
python3 ops/wq1406-paid-fallback-guard/tests/test_audit_fallback_chains.py
python3 ops/wq1406-paid-fallback-guard/tests/test_paid_fallback_alert.py
python3 ops/wq1406-paid-fallback-guard/tests/test_paid_spend_tripwire.py
```

All three run against fixtures/synthetic data — no live LiteLLM install,
proxy, or ntfy endpoint needed. `paid_fallback_alert.py` degrades
gracefully (`_HAVE_LITELLM = False`) when `litellm` isn't importable
(confirmed absent in this sandbox), same pattern as wq#1299's
`litellm_phoenix_sampler.py`.

## Deploy checklist (for whoever runs this on the gateway node)

1. `python3 audit_fallback_chains.py /home/lumen/agent/litellm_config.yaml`
   — fix any reported violation (reorder free-before-paid, or add a
   missing local rung) before proceeding.
2. Merge `litellm_config_snippet.yaml` into the live proxy config
   (append to `success_callback`, don't replace existing entries e.g.
   wq#1299's phoenix logger); deploy `paid_fallback_alert.py` onto the
   proxy's PYTHONPATH; restart the proxy.
3. Confirm the proxy startup log shows the custom callback loaded (exact
   log line is version-dependent — VERIFY).
4. Force one real fallback (kill/blackhole a primary deployment, same rig
   as wq#907's `accept_brain907.ts`) and confirm exactly one ntfy alert
   arrives, not one per request, and that a second forced fallback within
   30min does *not* re-alert.
5. `install.sh` — brings up `paid-spend-tripwire.timer`; confirm
   `systemctl list-timers paid-spend-tripwire.timer` shows it scheduled.
6. Run `paid_spend_tripwire.py --print` once by hand against the live
   `/spend/logs` endpoint to sanity-check real model names line up with
   the `PAID_PROVIDER_PREFIXES` classification (a renamed/aliased paid
   deployment could otherwise silently fall outside every check above).
