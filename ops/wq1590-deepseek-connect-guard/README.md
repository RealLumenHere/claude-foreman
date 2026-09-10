# wq#1590 — page on deepseek-v4-flash connect-failure spikes

FLEET_COST_PLAN.md D4: deepseek-v4-flash, the fleet's primary paid rung, ran
at a 59.2% failure rate over 35 days (94,339/159,474 requests) — every one of
them `litellm.InternalServerError DeepseekException - Cannot connect to host
api.deepseek.com:443`. Failed attempts bill nothing directly, but they drive
the retry+fallback machinery that does (wq#1406's DeepSeek/LPP runaway is
that machinery actually firing). Nobody caught this for 35 days because
nothing was watching failures on this rung in real time — it only surfaced
by reading a bill after the fact. This does not go away by moving other
workloads off DeepSeek, since it stays wired in as the fallback everywhere
else.

## Components

| File | Deploy target | Purpose |
|---|---|---|
| `deepseek_connect_alert.py` | onto the LiteLLM proxy's PYTHONPATH, registered as both `success_callback` and `failure_callback` | tracks a trailing-window connect-failure rate for `deepseek*` deployments (mirrors `dispatcher/circuit_breaker.py`'s MIN_SAMPLES + FAILURE_RATE_THRESHOLD shape) and pages ntfy, edge-triggered with a cooldown, the same day a spike starts |
| `litellm_config_snippet.yaml` | merge into the live proxy `config.yaml` (outside git, see below) | registers the callback |
| `tests/` | — | unit tests for every pure decision function, run standalone |

## Why a rate breaker, not a raw failure counter

A single connect failure is normal internet weather. What wasn't caught here
was a *sustained* 59% rate going unnoticed — so this reuses the exact
"windowed rate + minimum samples + threshold" shape `dispatcher/circuit_breaker.py`
already uses for fworker attempts (wq#718), applied here to per-call
connect failures on deepseek deployments instead. Default threshold is 50%,
deliberately below the 59.2% this ticket measured, so the real incident would
have tripped it with margin; default window is 90 minutes, same as
`circuit_breaker.py`'s default.

## Run the tests

```
python3 ops/wq1590-deepseek-connect-guard/tests/test_deepseek_connect_alert.py
```

## Deploy

1. Copy `deepseek_connect_alert.py` onto the LiteLLM proxy host, on its PYTHONPATH.
2. Merge `litellm_config_snippet.yaml` into the live proxy `config.yaml` (that
   file lives outside this repo, on the gateway node — same convention as
   wq#897/wq#948/wq#1299/wq#1406).
3. Restart the proxy so the callbacks register.
4. VERIFY: the exact kwargs shape LiteLLM's success/failure callbacks pass,
   and where the raised exception's string lives on the failure path, are
   version-dependent — confirm against the live proxy the first time a real
   connect failure fires (same caveat wq#1299/wq#1406 already flagged for
   their callbacks).
