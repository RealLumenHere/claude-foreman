# wq#4692 — metered-rung opt-out for the LiteLLM rung probers

model-watchdog (every ~15 min) and litellm-completion-probe (every ~30 min,
sentinel-3 key) both enumerate every rung in litellm_config.yaml and sent a
real, billed chat completion to each — including the per-token Anthropic
rungs (`lumen/claude-sonnet`, `lumen/claude-haiku`). SpendLogs: 1318 calls /
$0.1216 in the last 7d, against Frank's standing no-spend rule (fleet jobs
don't route to `lumen/claude-*` unless he directs it).

## Fix (class-level, both callers)

`ops/wq3212-litellm-dead-rungs/litellm_rungs.py` — the ONE shared rung-list
module both probers already read from (wq#3212) — gains:

- `_metered(entry)`: a rung is metered when an explicit
  `model_info.metered` overrides it either way, else when
  `litellm_params.api_key` pulls `ANTHROPIC_API_KEY` from the environment
  (the live shape: `ops/wq4484-litellm-haiku-model-fix/litellm_config_snippet.yaml`).
- `metered_names(config_path)`: the set of eligible metered model_names.
- `Rung` gains a `metered` field, populated by `one_rung_per_host`.

Both callers now skip the real completion for a metered rung:

- `ops/wq2903-model-ref-drift/lumen_model_watchdog.py` (`run()`): a roster
  member in `config_metered_names()` is marked up without calling `probe()`
  — membership in `roster` already proves it answered `GET /v1/models`
  (the existing `split_drift` pre-filter), so no second network call is
  needed either.
- `ops/wq3169-litellm-completion-probe/litellm-completion-probe`
  (`probe_one`): a metered `Rung` is routed to the new
  `check_model_present()`, a `GET /v1/models` presence check, instead of
  `POST /v1/chat/completions`.

Rungs are never deleted — only the probe strategy changes.

## Tests

- `ops/wq3212-litellm-dead-rungs/tests/test_remove_dead_rungs.py`:
  `test_metered_names_reads_api_key_env_ref_and_model_info_override`,
  `test_one_rung_per_host_carries_metered_flag`.
- `ops/wq2903-model-ref-drift/tests/test_watchdog_drift.py`:
  `test_metered_rung_is_never_sent_a_real_completion` (fails the moment
  `probe()` is called for a metered rung — the acceptance-probe-must-fail
  gate wq#2630 asks for),
  `test_metered_rung_absent_from_live_is_drift_not_a_probe`.
- `ops/wq3169-litellm-completion-probe/tests/test_litellm_completion_probe.py`:
  `test_metered_rung_never_sends_a_completion` (asserts the request never
  hits `GATEWAY_URL`), `test_metered_rung_absent_from_models_is_a_fail`,
  `test_unmetered_rung_still_sends_a_completion` (control — a normal rung
  must still be probed for real).

Run: `python3 -m pytest ops/wq3212-litellm-dead-rungs/tests/test_remove_dead_rungs.py
ops/wq2903-model-ref-drift/tests/test_watchdog_drift.py
ops/wq3169-litellm-completion-probe/tests/test_litellm_completion_probe.py -q`
→ 42 passed.

## Deploy

Not run from this BUILD worktree (requires root via `lumen-run --root`, out
of scope for a build ticket). The existing
`ops/wq3212-litellm-dead-rungs/install.sh` already deploys exactly these
three files (`litellm_rungs.py`, `litellm-completion-probe`,
`lumen_model_watchdog.py`) to `/home/lumen/bin` / the watchdog's live path —
re-running it after this PR lands picks up the fix with no further changes
needed to that script.

## HANDOFF s6 correction (hand-merge required)

`/home/lumen/HANDOFF.md` is outside this worktree's allowed-dirs (confirmed:
same boundary `ops/wq1406-paid-fallback-guard/litellm_config_snippet.yaml`
already hit for `/home/lumen/agent`), so this is a fragment to hand-merge,
not a diff — same convention as that ticket.

HANDOFF section s6 claims the `lumen/claude-*` aliases were never tested
with a live completion. SpendLogs on `lumen-pgvector` (db `litellm`) refute
this directly: `lumen/claude-sonnet` carries 1318 calls / $0.1216 USD in the
last 7d (model-watchdog 984, sentinel-3 331, ops-emergency 3), 1508 calls /
$0.1391 USD since 2026-07-10 — all real `/chat/completions` traffic, not a
one-off manual test. Replace the s6 claim with:

> `lumen/claude-*` HAS been exercised with live completions — not
> deliberately, but as an unintended side effect of the LiteLLM rung
> probers (model-watchdog, litellm-completion-probe) treating every
> configured rung as free local capacity before wq#4692. Both probers now
> confirm a metered rung via `GET /v1/models` presence only; see
> `ops/wq4692-litellm-no-spend-probe-opt-out/README.md`.
