# wq#3789 — adversarial verify + judgment on wq#3783 (hathor-aware judge fallback routing)

- **Ticket verified:** wq#3783, PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1783, merged `8d7fce65` (`fix(judge): hathor-aware fallback routing`)
- **Verifier:** claude-sonnet-5@high (fresh instance)
- **Escalation:** opus-5@high (contradiction found → judgment trigger)
- **Verdict: CONTRADICTION CONFIRMED (moderate severity) — follow-up fix required, NOT a revert.** Follow-up ticket filed: wq#3792.

## Scope

wq#3783 touched `judge/judge_common.py` (added `pinned_node_reachable()`, `resolve_judge_model()`),
`judge/JUDGE_MODEL_FALLBACK` (new pin file, `nephthys-qwen14b`), and
`judge/test_judge_fallback_routing.py`. The downstream wiring of `bin/judge` to
`resolve_judge_model` is a separate, already-merged ticket (wq#3788, PR
lumen-bin#247) and is out of scope for this verify-pass except where its code
is needed to show the real-world effect of wq#3783's own gap.

## What works

- `test_judge_fallback_routing.py`'s 5 tests pass as written (`pytest judge/test_judge_fallback_routing.py` — 5 passed).
- The core scenario the ticket asked for — hathor asleep must not fail the gate closed by itself — works: `resolve_judge_model()` probes the primary rung's Ollama `/api/ps` with a 5s timeout and correctly routes to the fallback pin when the primary is unreachable, falling back to the pre-fix behavior when no fallback is configured.
- `model_source` is threaded through to `log_verdict()` so the routing decision is auditable in `minerva.db`.

## Contradiction found and confirmed

`resolve_judge_model()` returns one of three `source` prefixes: `env:`, `pin:`, `fallback:`.
`bin/judge` (wq#3788) uses `required=model_source.startswith("pin:")` to decide whether
`verify_model_identity()` (wq#2821 — catches a rung silently re-tagged to serve different
weights) must pass. That `required=` test predates wq#3783 and was written for a two-way
split: `pin:` (must verify) vs. `env:` (deliberate human override — exemption intentional,
documented as "optional for an explicit JUDGE_MODEL override (bench/experiments)").
wq#3783 adds a third source, `fallback:`, which falls into the same exemption as `env:` —
but a fallback is not a deliberate human override, it is an automatic, unattended routing
decision made by the system itself, during exactly the unattended hours this fix exists to
cover.

`judge/JUDGE_MODEL_IDENTITY.json` has no entry for `nephthys-qwen14b` (the pinned fallback).
Live probe:

```
>>> verify_model_identity('nephthys-qwen14b', required=False)   # what bin/judge calls when source="fallback:..."
None    # no error -- treated as fully verified
>>> verify_model_identity('nephthys-qwen14b', required=True)
'no identity pin for nephthys-qwen14b in /home/lumen/foreman/judge/JUDGE_MODEL_IDENTITY.json'
```

`call_judge()` itself never checks model identity either — it only POSTs the model name to
the LiteLLM gateway. So today: if nephthys is ever silently re-tagged (the exact 2026-09-28
failure mode wq#2821 exists to catch) while hathor happens to be asleep, the judge gate will
trust whatever nephthys serves with zero identity check and zero fail-closed signal. No test
covers this — `test_judge_fallback_routing.py` only exercises `resolve_judge_model()`'s
return values, never `bin/judge`'s downstream `required=` branch with a `fallback:` source.

## Lesser finding (context, not independently a contradiction)

`resolve_judge_model()` never calls `pinned_node_reachable()` on the fallback itself —
it blindly returns the fallback once the primary fails its probe. Reproduced live: with
both primary's and fallback's `/api/ps` raising `OSError`, `resolve_judge_model()` still
returns `("nephthys-qwen14b", "fallback:...", "primary rung 'hathor-llama8b' unreachable,
routed to fallback 'nephthys-qwen14b'")` with no hint the fallback is also dead. The
function's docstring promises finding a dead node "in ~5s, not ~240s" — that promise is
only kept for the primary; a dead fallback still pays the full `call_judge` timeout before
eventually fail-closing. Mitigated in practice: `research/inference_nodes.yaml` has
`nephthys: power_policy: warm` (not an auto-sleep Mac like hathor), so the double-down case
is uncommon. The test file's own docstring claims the fix "requires BOTH primary and
fallback unreachable" to fail closed — true only via `call_judge`'s eventual failure, never
asserted by a test, and (per the opus judgment) can't be exercised as designed since the
missing identity pin is the same root cause: `pinned_node_reachable()` also returns `True`
unconditionally for any model with no identity entry (lines 295-296), so nephthys can never
be observed "unreachable" by that probe either way.

## opus-5@high judgment (verbatim verdict)

> **VERDICT: CONTRADICTION CONFIRMED.** It's moderate in severity. The fix should be a fast
> follow-up, not a revert. ... wq#3783's main goal does work: a sleeping hathor no longer
> blocks the gate. That's why this should be a follow-up rather than a revert. A revert
> would bring back the fail-closed-on-sleep outage. But "done" shouldn't stand without a
> linked follow-up ticket, because the fix weakens an existing safety guarantee on an
> unattended path and no test covers it.

Full subagent response is reproduced in spirit above (background, rationale, recommendation);
condensed here to avoid duplicating ~300 words verbatim.

## Disposition

- wq#3783 stays **done** — its stated goal (hathor asleep must not fail the gate closed by
  itself) is correctly delivered, and reverting would restore the original outage.
- Follow-up filed: **wq#3792** — add a `nephthys-qwen14b` identity pin, change `bin/judge`'s
  `required=` to cover every non-`env:` source (not just `pin:`), add the two missing test
  cases (fallback with no identity pin → fail closed; both primary and fallback
  unreachable).

## Unrelated issue logged in passing

During this verify-pass, `lumen_busy update` clobbered a concurrent fworker's task label
(ISSUE-LOG row 1278): `lumen_busy` is a single global state file with no per-session
scoping/locking, so two fworker sessions racing `set`/`update` corrupt each other's record.
Worked around (checked the dead PID, cleared the key); no code fix applied — out of scope
for this ticket.
