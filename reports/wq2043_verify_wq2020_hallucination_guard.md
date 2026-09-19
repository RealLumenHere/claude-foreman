# wq#2043 — verify-pass of wq#2020 (hallucination guard, PR 657, commit bf83468)

**Verdict: CONFIRM** — the guard does what wq#2020 claims. Two operational defects found;
neither contradicts the claim, both filed as wq#2045.

## What was run

| check | result |
|---|---|
| `pytest harness/tests/test_hallucination_guard.py` | 20 passed |
| `pytest harness/tests -k "acceptance or verify or fworker"` | 79 passed (6m22s, see defect 1) |
| live probe, true claim (`mode() returns advisory by default` vs `hallucination_guard.py:54-56`) | `supported`, verdicts 3/3, confidence 0.9827 |
| live probe, false claim (`mode() returns enforce by default`, same source) | `unsupported`, verdicts 3/3, confidence 0.9971 |
| hung-server probe (socket that accepts and never replies, `REQUEST_TIMEOUT` patched to 2s) | `inconclusive`, not an exception; but see defect 2 |
| read of `read_source` | realpath containment check rejects `..` and symlink escapes |
| read of `acceptance.verify` / `fworker.verify_done` wiring | claim check runs only after a clean mechanical replay; only `enforce` extends `failed`; Stop-hook path passes no `claim_check` |

Live probes ran against isis ollama (`qwen3.6:35b-a3b`), ~28s per claim (3 samples).

## Defects (wq#2045)

1. **Tests are not hermetic.** Default mode is `advisory`, which still calls isis. The
   fworker integration tests reach `verify_done` and pay for it:
   `test_success_settles_spawn_reservation` takes 24.2s at default vs 4.35s with
   `FOREMAN_HALLUCINATION_GUARD=off`. No conftest pins the guard off. If isis is down or
   slow the suite slows with it.
2. **No circuit breaker.** `claim_check` loops `check_claim` per claim with no
   short-circuit after the first `inconclusive`. A refused connection fails fast, but a
   hung isis costs `REQUEST_TIMEOUT` (90s) per claim, serially, up to `MAX_CLAIMS` (6):
   about 9 minutes added to one `verify_done`. This undercuts "a down free tier must not
   stall the fleet" for the hang case (measured: 90.1s for one claim against a silent
   socket).
3. (design note, not filed as a bug) With no worker-written `claim`, the fallback claim is
   the criterion text, which is often not a statement the cited lines can prove. In
   `enforce` mode that would flag valid work. Advisory default makes this harmless today;
   do not flip to `enforce` without advisory data.

## ISSUE-LOG row (kb is read-only for workers; land this row when the kb is next synced)

| 439 | wq#2043 verify of wq#2020: guard defaults to advisory, so every fworker `verify_done` and the fworker integration tests call live isis qwen (24s vs 4.4s per test; 79-test subset 6m22s); a hung isis costs 90s per claim x up to 6 serially per verify. Verify lane also refused `rm`/`unlink` of its own probe file, an `env VAR=x cmd` prefix, and Write/Edit under `/home/lumen/kb` | `claim_checker()` honours only the env var, tests never pin it off, no circuit breaker after first inconclusive; kb is a primary tree (wq#1719) | wq#2045 filed for conftest autouse off + short-circuit. Workarounds: `git clean -fq <file>`, in-process `pytest.main` with `os.environ` set | open |

## Follow-up (wq#2045)

1. `harness/tests/conftest.py` pins `FOREMAN_HALLUCINATION_GUARD=off` for every test (autouse); the guard's own tests inject `check=` or set the mode.
2. `claim_check` stops after the first `inconclusive` verdict and marks the remaining claims `skipped` (`harness/hallucination_guard.py`, new `skipped` counter; covered by `test_first_inconclusive_short_circuits_the_remaining_claims`).
3. Criterion-text fallback stays advisory: default mode is unchanged and `enforce` is not enabled anywhere. Revisit once advisory verdict data exists.
