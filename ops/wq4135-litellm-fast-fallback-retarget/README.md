# wq#4135: `lumen/fast` still 500 after wq#4102 — its only fallback rung is dead

wq#4102 fixed the alias-bypass so `lumen/fast` inherits `lumen-lfm2-1b`'s
fallback chain. That chain is `[cerebras-llama]`, and on 2026-10-17 probes
show the chain is loaded but the rung cannot answer, so a primary failure
still surfaces as 500.

## Probed facts (probe_rung.py, real 1-token completions, 2026-10-17)

| rung | result |
|---|---|
| `cerebras-llama` | **402** `CerebrasException - Payment required` — dead since the Cerebras billing lapse (2026-08-19, lumen_state lpp.brain_escalate). It has no chain of its own, so the walk ends there. |
| `qwen36-local` | **400** `Invalid model name` — the ticket's suggested rung does not exist in the gateway. |
| `deepseek-v4-flash` | 200 **only via its own fallback** to `openrouter-free` (`x-litellm-attempted-fallbacks: 1`; direct DeepSeek balance exhausted, wq#3410). A fallback rung is invoked as a plain group call inside the chain walk, so pointing `lumen/fast` at a group that itself needs a fallback adds a known-failing paid hop. |
| `openrouter-free` | **200 direct**, `attempted_fallbacks: 0`. |
| `lumen-lfm2-1b` | 200 direct (the primary is healthy; the chain only matters when it fails). |

## Decision

Retarget `lumen/fast` and `lumen-lfm2-1b` fallbacks `cerebras-llama -> openrouter-free`.
Both of the ticket's suggested targets fail probing (table above); `openrouter-free`
is live, free-tier (satisfies the wq#1406 free-before-paid rule, unlike a
cerebras or deepseek rung), and already the fallback for the other four cheap
groups (`deepseek-v4-flash`, `deepseek-chat`, `gpt-3.5-turbo`, `messenger-chat`),
so this makes the config more uniform, not less. One-hop-to-live-openrouter is
the exact shape `lumen/best -> [openrouter-deepseek-pro]` already proves works.

`retarget_rung.py` swaps a dead rung for a live one in every
`router_settings` fallback kind, with wq#4102's safety pattern: line-level
edit, re-parse-and-compare before writing, backup, in-place write (bind-mount
inode). If a chain already lists the replacement, the dead line is dropped
rather than duplicated.

Out of scope, noted: `context_window_fallbacks: lumen-lfm2-1b/lumen/fast ->
[deepseek-v4-flash]` still points at the 402-dead direct-DeepSeek group.
Context-window overflow on a 1B local model is real but that chain predates
this ticket and changing it is a routing decision for a separate ticket.

## Reload

LiteLLM reads the config only at process start, and workers may not touch
containers, so `reload_and_verify.sh` is queued as fleet-window op
`wq4135-litellm-fast-retarget` (node lumen). It **replaces** queued op
`wq4102-litellm-alias-reload`: that op's config edit is already live (the
04:37Z 2026-10-17 restart loaded the alias chains — ticket evidence), and its
verify probes `lumen/fast`, which cannot pass while cerebras-llama is dead, so
left in place it would fail and stop the drain. This op is a superset: it
re-runs `alias_fallbacks.py apply` (idempotent) before the retarget, prechecks
that the replacement rung answers, restarts once only if the running gateway
does not already fall back, and verifies with wq#4102's mock-fallback probe on
`lumen/fast`, `lumen-lfm2-1b`, `lumen/best`, `deepseek-v4-pro`.

Tests: `python3 ops/wq4135-litellm-fast-fallback-retarget/tests/test_retarget_rung.py`
(runs against wq#4102's committed live-shape fixture, including the exact live
sequence: alias chains added, then the dead rung swapped in both chains).
