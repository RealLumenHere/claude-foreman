# wq#2593 — verify of wq#2573 (minerva PR#32, commit `b3259ca`)

Verifier: claude-sonnet-5@high, fresh instance (same model as author). Verdict:
**CONTRADICT** (one blocking finding; everything else confirmed). Escalated per
archetype 8 to opus-5@high — **wq#2596** filed, `deps=2573`. Full ISSUE-LOG entry: #714.

Note on repo location: wq#2573's minerva deliverable (`lumen_reflector.py`) lives in
`minerva`, not this (`foreman`) repo — same cross-repo dispatch quirk logged as
ISSUE-LOG #708/#710. PR#32 is already merged to minerva `main` (`b3259ca`). Verified
by cloning minerva read-only into scratch (`/tmp/fworker-wq2593/minerva-ro`, no writes
made to the primary), running the shipped `test_lumen_reflector.py` directly against
the merged source, and writing a standalone adversarial probe alongside it (not
committed anywhere).

## Per claim

| wq#2573 claim | Result | Evidence |
|---|---|---|
| `alert()` no longer swallows a failed ntfy POST with bare `except: pass` | **CONFIRMED** | `lumen_reflector.py:222-227`; now logs to stderr. `test_alert_failure_does_not_raise` passes. |
| `write_run_state()` records status + refreshes the liveness stamp | **CONFIRMED** | `lumen_reflector.py:230-242`; `test_write_run_state_ok_then_error_overwrites` passes. |
| Explicit `main()` failure branches (no key, LLM error) write `status:error` before `sys.exit` | **CONFIRMED** | `lumen_reflector.py:247-260`; `test_main_no_key_writes_error_state_and_exits_2` and `test_main_llm_error_writes_error_state_and_exits_1` pass. |
| Success path writes `status:ok` | **CONFIRMED** | `lumen_reflector.py:294`; `test_main_success_writes_ok_state` passes. |
| Test suite: 5/5 pass | **CONFIRMED, independently re-run** | Cloned minerva at `b3259ca` into `/tmp/fworker-wq2593/minerva-ro`, ran `python3 -m pytest -q test_lumen_reflector.py` directly — 5 passed, 0 failed, matching the PR's claim. |

## Blocking finding: `write_run_state()` is not actually on "every exit path" — uncaught exceptions bypass it entirely

The module comment (`lumen_reflector.py:19-26`) and `write_run_state()`'s own
docstring (`lumen_reflector.py:49-51`) both state the intent broadly: write a
liveness+status signal "on every exit path" / "unconditionally", specifically so
`cron_deadman.py` doesn't need to wait on stamp age to notice a failure. But the
implementation only calls `write_run_state()` at three specific points in `main()`:
the no-key branch, the LLM-error branch, and the success tail. It is never wrapped
around `main()`'s body as a whole, so any exception a helper raises *outside* those
two explicit `if err:` checks propagates straight past both call sites.

Two concrete, realistic paths do exactly that:

- `recent_episodes()` (`lumen_reflector.py:72-81`): `sqlite3.connect(str(DB))` plus
  the `SELECT ... FROM archival_notes` call have **zero exception handling**. A
  missing DB file, a lock held by a concurrent writer, or a schema change (column/
  table renamed) all raise straight out of `main()`.
- `_post()` (`lumen_reflector.py:175`): `r.json()['choices'][0]['message']` on a
  `200` response with an unexpected body shape (e.g. an error payload the gateway
  returns with a 200 status, or a provider-format mismatch) raises `KeyError`/
  `IndexError`/`TypeError`, none of which `call_llm`'s `if err:` check catches — it
  only inspects the `err` string `_post` explicitly returns.

Live-probed against the exact merged source (not hypothetical — ran it):

```
$ python3 -m pytest -q test_adversarial.py
.                                                                        [100%]
1 passed in 0.02s
```

The probe (`test_uncaught_exception_in_recent_episodes_leaves_no_state`): seeds
`write_run_state('ok', 'prior successful run')` (simulating a healthy prior run, as
would exist in prod), monkeypatches `recent_episodes` to `raise RuntimeError(
'database is locked')`, calls `lr.main()` — which raises, uncaught, exactly as it
would under systemd — then reads back `.state.lumen-reflector`. It still says
`status: "ok"`. The test asserts that (documenting the bug); if wq#2573's claim held,
the assertion would need to check for `"error"` instead, and it doesn't get the
chance to.

This is not a cosmetic gap — it reopens the exact failure mode wq#2573 was filed to
close, for a whole class of failures instead of the two that got explicit handling.
`cron_deadman.py`'s new immediate-page path (same wq#2573 companion change,
`find_run_states`/`cron_deadman.py:170-185`) reads `status` purely from
`.state.lumen-reflector`; if that file is never updated, `is_error` stays `False`,
no immediate page fires, and the unit falls back entirely to the pre-fix,
age-based staleness check — the 14-day blind spot (2x this weekly timer's cadence)
that let the original 2026-09-20 failure sit unticketed for 5 days.

Scope of the contradiction: narrow. The ntfy-swallow fix, the two explicit
error-branch writes, the success-path write, and the state-file schema itself are
all sound and independently reproduced. Only the missing catch-all around `main()`
(or per-helper guards) is wrong.

## Attempts to break the fix that did NOT find a defect

- `write_run_state()`'s own `try/except OSError` (disk full, permission denied on
  `/home/lumen/logs`) is handled — logs to stderr, doesn't raise a second exception
  on top of whatever triggered the write. Not a defect.
- The atomic `tmp.write_text()` + `tmp.replace(RUN_STATE)` pattern avoids a reader
  ever observing a half-written JSON file. Confirmed by inspection; no partial-write
  race.
- `RUN_STAMP.touch()` happening unconditionally inside `write_run_state()` (not
  gated on `status`) correctly refreshes staleness for both success and the two
  explicitly-handled failures — matches intent for those three paths specifically.
- `_extract_json`'s tolerant parsing (fence-stripping, trailing-comma repair) does
  not swallow a truly-malformed response silently; `call_llm` still returns a
  non-None `err` in that case, hitting the (correctly handled) explicit branch.
- Retested `test_main_llm_error_writes_error_state_and_exits_1` and
  `test_main_success_writes_ok_state` with monkeypatched `recent_episodes`/
  `latest_dream`/`growth` returning edge-case empty values (`[]`, `''`, `(0, 0)`) —
  no crash, no defect in the happy/explicit-error paths themselves.

## Actions taken

| Action | Rationale |
|---|---|
| ISSUE-LOG **#714** appended | Records the uncaught-exception gap and the reproduction, per doctrine |
| **wq#2596** filed, pri 1, `deps=2573`, `claude-opus-5@high` | Judgment ticket per archetype-8 protocol: decide try/except-wrap-`main()` fast-follow vs. reopening wq#2573 vs. accepting the residual risk |
| wq#2573 left `done`/PR#32 left merged, not reopened or edited here | Verify-only per archetype 8 — do not re-implement; opus-5@high judgment decides disposition |
