# wq#2046 — verify-pass of wq#2045 (guard pinned off in tests + circuit breaker, PR #660, commit 8397e56)

**Verdict: CONFIRM** — both shipped fixes do what wq#2045 claims. No contradiction, no escalation.

## What was run

| check | result |
|---|---|
| `pytest harness/tests/test_hallucination_guard.py` | 21 passed (0.11s) |
| `pytest harness/tests/test_fworker_integration.py -k test_success_settles_spawn_reservation` (conftest active) | 4.37s |
| same test with `--noconftest` (guard defaults to advisory, live isis ollama) | 15.3s |
| `pytest harness/tests -x` | 1140 passed, 1 failed (`test_wq_worker_slot.py::test_second_holder_is_refused_while_the_first_is_running`); the file passes 6/6 in isolation, so the failure is unrelated to this diff (ISSUE-LOG #439) |

## Findings

1. **Pin off in tests: confirmed.** `harness/tests/conftest.py` sets `FOREMAN_HALLUCINATION_GUARD=off` autouse. `mode()` reads the env at call time (`hallucination_guard.py:54`), and `claim_checker()` returns `None` when off (`:324-326`), so `acceptance.verify` never calls the model. The 4.4s vs 15.3s delta reproduces the wq#2043 defect (24s there, less load now). The guard's own env-mode test (`test_hallucination_guard.py:223-227`) still works because it sets the env itself.
2. **Circuit breaker: confirmed.** `claim_check` (`hallucination_guard.py:299-306`) stops after the first `inconclusive` and records the rest as `status: skipped`. `check_claim` returns `inconclusive` only when the model is unreachable (`OSError`, `:248-249`), which is a model-wide condition, so skipping the remaining claims cannot mask a claim-specific verdict. `unsupported` and `unstable` do not trip it. `flagged` stays empty for skipped entries, so enforce mode cannot fail the gate on them. `test_first_inconclusive_short_circuits_the_remaining_claims` asserts one call, statuses `[inconclusive, skipped, skipped]`, counters 1/2 and no flags.
3. **Consumers of `checked`: no breakage.** Only `acceptance.py` passes the dict through; `fworker.py:2732-2736` logs it and now includes `skipped`.
4. **Advisory default unchanged; enforce not enabled anywhere.** Item (3) of wq#2045 is satisfied.

## Notes (not blocking)

- The breaker is per `claim_check` call. Each `verify_done` can still cost one `REQUEST_TIMEOUT` when isis is hung (down from up to `MAX_CLAIMS` × timeout); a cross-call cooldown would be a separate ticket.
- `test_wq_worker_slot` flaked once in a 6-minute full run on a busy node (ISSUE-LOG #439).
