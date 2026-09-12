# wq#1866 — Adversarial verify-pass of wq#1865 (despag P1-7: burn-rate ceiling no-op fix)

**Verdict: CONTRADICT** — `despag/admission.py` (commit `5bedd3f`, PR #548) does stop the
literal bug wq#1864 found (`allowed is None` → unbounded, `running_claude_max=500` still
`allow:True`). But the replacement fallback is not the "binary tight clamp" the fix's own
comment and test claim it mirrors, and it reopens a bypass of `FRANK_RESERVE_PCT` — the
exact property wq#1812 requires "never" bypassed — whenever `resets_at` is missing while
`percent` is already past the reserve.

## What the fix does (despag/admission.py:229-237)

```python
if allowed is None:
    # ... which is what foreman_dispatcher.py:1411-1417 does via tight_mode.
    allowed, detail = DESPAG_MAX_CLAUDE_CONCURRENT, f"{detail}; clamped to DESPAG_MAX_CLAUDE_CONCURRENT"
if running_claude_max >= allowed:
    return {"allow": False, ...}
```

When `burn_rate.allowed_concurrent` can't compute a rate (no pool has both `percent` and
`resets_at`), the gate now clamps `allowed` to the raw `DESPAG_MAX_CLAUDE_CONCURRENT`
config constant (default 8) instead of skipping the check. This closes the literal
unbounded hole. Confirmed via `despag/tests/test_admission.py::test_missing_resets_at_falls_back_to_the_max_concurrent_clamp`
and a local rerun (12/12 admission tests pass).

## Where it breaks: the claimed mirror of `foreman_dispatcher.py:1411-1417` is false

The fix's comment (admission.py:229-236) and the module docstring addition both say the
`DESPAG_MAX_CLAUDE_CONCURRENT` clamp on `None` "mirror[s] the dispatcher's tight-mode
fallback on the same None." It doesn't. Read `dispatcher/foreman_dispatcher.py:1407-1417`:
on `allowed is None` the dispatcher does **not** substitute any concurrency number — it
sets `tight = True`, which caps `spawned >= tight_cfg["max_spawns_per_pass"]` (a small,
config-driven spawns-per-pass limit, unrelated to `DESPAG_MAX_CLAUDE_CONCURRENT`) and
additionally filters candidates to `epri <= tight_cfg["min_pri"]` only. `DESPAG_MAX_CLAUDE_CONCURRENT`
never appears in that code path — grep confirms the dispatcher's only reference to the
constant's despag-side counterpart is absent; the two modules don't share a fallback
value at all. The fix's cited justification for the specific number it falls back to is
incorrect.

## Where it breaks: `DESPAG_MAX_CLAUDE_CONCURRENT` becomes the bypass wq#1812 forbids

wq#1812's ticket text and `admission.py`'s own module docstring (lines 21-24) both state
the constant is "only an upper clamp, never a bypass" of the `FRANK_RESERVE_PCT`-aware
ceiling. In the `allowed is None` branch, that's exactly backwards: `DESPAG_MAX_CLAUDE_CONCURRENT`
*is* the entire decision — the reserve-aware calculation is never consulted, even when
`percent` (the actual usage signal) is present and already past reserve. `resets_at`
going missing is orthogonal to how much budget is left; the fix conflates "can't compute
a rate" with "assume there's plenty of room."

Repro (mirrors the existing `test_reserve_honoured_remaining_below_reserve_refuses` setup —
session 75% used / 25% remaining, under the 30% session reserve — but drops `resets_at`,
the same "real shape" the fix's own comment cites, e.g. right after a governor poll
restart repopulates `percent` before `resets_at`):

| running_claude_max | `resets_at` present (reserve honoured) | `resets_at` missing (this fix) |
|---|---|---|
| 0 | allow (headroom = MIN_ALLOWED=1) | allow |
| 1 | **refuse** (`allowed=1`) | allow |
| 5 | **refuse** | allow |
| 7 | **refuse** | allow (refuses only at 8) |

Identical account usage, identical reserve state — the only variable is whether
`resets_at` happened to be populated on this poll. With it present the gate correctly
holds the account to 1 concurrent claude-max turn (reserve-starved). With it missing,
the same account is allowed up to 7x that, because the fallback substitutes a static
constant for the reserve calculation instead of failing tight. This is precisely the
"draining Frank's Max subscription silently" scenario wq#1812 was filed to prevent,
reopened in a narrower (bounded instead of unbounded) but still real window.

`despag/tests/test_admission.py`'s new test only checks the `allowed is None` fallback
at low usage (`percent: 5` on both pools) — it never combines "missing `resets_at`" with
"usage already past reserve," so it can't see this gap; all 12 tests pass with the bug
present.

## What's correctly fixed

- The literal unbounded hole wq#1864 reported (`running_claude_max=500` → `allow:True`)
  is gone; the gate now refuses at `DESPAG_MAX_CLAUDE_CONCURRENT` regardless of scale.
- `evidence.json` and the new test both exercise the boundary at `DESPAG_MAX_CLAUDE_CONCURRENT - 1`
  / `DESPAG_MAX_CLAUDE_CONCURRENT` correctly for the low-usage case they cover.

## Recommendation

The correct fallback is the one the fix's own comment claims to implement: fail toward
the dispatcher's actual tight-mode behavior (or equivalently, degrade to `burn_rate.MIN_ALLOWED`
rather than `DESPAG_MAX_CLAUDE_CONCURRENT`) when no pool has usable `percent`+`resets_at`
data — the same "fail closed" posture `admission.py:216-219` already uses for a governor
read failure two branches up. Filing wq#1867 (claude-opus-5@high, judgment) to fix.
