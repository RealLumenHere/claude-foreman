# wq#2497 — verify of wq#2491 (PR 908, 1211aa7 / squash 6057f8a1ff)

Verifier: claude-sonnet-5@high (fresh instance). Verdict: **CONFIRMED**.

## Per claim

| wq#2491 claim | Result | Evidence |
|---|---|---|
| Test proves a SIGKILLed worker's ticket is reclaimed, not stranded | **CONFIRMED (reproduced independently)** | `python3 -m pytest tests/test_wq2491_sigkill_chaos.py -q` (run from `harness/`, matching the repo's documented convention) → 2 passed, repeated 3x, no flakiness |
| Reclaim is logged loudly (bin/wq:34) | **CONFIRMED (reproduced independently, outside the test)** | Manual repro against a throwaway scratch `WQ_DB`: `REAPED #1 lane=auto holder=lumen:99999 claimed_ago_min=0 lease_expired_ago_min=5 title='manual repro ticket'` / `reaped 1 ticket(s)` — matches the test's assertion pattern exactly |
| Negative case: a LIVE holder is never reclaimed no matter how quiet (bin/wq:29) | **CONFIRMED** | `test_live_lease_is_never_reclaimed_no_matter_how_quiet` — SIGKILLs the same way, does not backdate `lease_expires`, asserts `reaped 0 ticket(s)` and ticket stays `claimed`. Traced against `cmd_reap` (bin/wq:3229-3231): the sweep predicate is `lease_expires IS NOT NULL AND lease_expires < now()` only — no liveness/heartbeat signal enters it, so a dead-but-unexpired holder is architecturally unreachable by this path |
| Ticket's `WQ_STALE_MIN` premise is wrong; the real knob is `lease_expires`/`WQ_PROVISIONAL_MIN` | **CONFIRMED** | Read bin/wq's own docstring (lines 27-34): `WQ_STALE_MIN` frees a BASE NAME/session identity only; `cmd_claim` housekeeping (1556-1558) and `cmd_reap` (3229-3231) both key off `lease_expires`. Correction is also logged at `kb/ISSUE-LOG.md:755` (#657), which exists and matches the PR's own description of it |
| Blast radius bounded: scratch `WQ_DB`, only PIDs the test itself spawned | **CONFIRMED** | `bin/wq:101`: `DB = os.environ.get("WQ_DB", ".../queue.db")` — env var fully governs the DB path; test sets it to `tmp_path`. `spawn_fake_worker`/`sigkill_and_reap_own_worker` only ever touch `worker.pid`, captured from its own `Popen` |
| `wq add` output parsing (`p.stdout.strip().split()[2]`) is correct | **CONFIRMED** | Reproduced manually: `wq add --title ...` → ticket id is the 3rd whitespace token |
| PR actually merged, diff matches what's on `main` | **CONFIRMED** | `git patch-id` of worktree commit `1211aa7` and main's squash `6057f8a1ff` both hash to `76f4de3f...` — byte-identical content, not just same message |

## Attempts to break the fix

- **Wrong invocation direction**: running `python3 -m pytest harness/tests/test_wq2491_sigkill_chaos.py -v` from the repo root fails (`ModuleNotFoundError: fworker`) because `harness/tests/conftest.py`'s autouse fixtures import `fworker`/`capability_proposal`/`scratch_grant`, which only resolve when `harness/` itself is on `sys.path` (i.e. invoked as `cd harness && python3 -m pytest tests/...`, the convention already used in other merged PR bodies in this repo, e.g. `pr_body_wq2116.md`). This is an environment/invocation fact, not a defect in the PR — every other file under `harness/tests/` has the identical constraint.
- **Provisional-lease cap on the negative test**: `claim --ttl 240` doesn't actually grant a 240-minute lease — `cmd_claim` caps the first lease at `min(PROVISIONAL_MIN, ttl)` = 60m (bin/wq:1665-1666). The negative test's assertion still holds regardless (60m is still far in the future relative to the test's runtime), so this doesn't break the test, but it's worth flagging as a latent gap: the test doesn't independently exercise the *promoted* full-ttl lease path, only the provisional one. Not a defect in what's claimed — the ticket only asked for "a live holder is never reclaimed," which holds under either lease length.
- **Timezone/clock mismatch between the test's `force_expire` and `bin/wq`'s `now()`**: both use `datetime.now(timezone.utc)` / `utcnow()` with the same `%Y-%m-%d %H:%M:%S` format — no drift possible from a local/UTC mismatch.
- **Reap sweeping other lanes/rows**: scratch `WQ_DB` starts empty per test (fresh `tmp_path`), and each test seeds exactly one row, so there's no cross-test or cross-lane contamination to hide a bug.
- **Flakiness**: reran the target file 3x back-to-back, all green; also ran it alongside its stated companions (`test_wq_reap.py`, `test_reaper.py`) — 9 passed, no interaction effects.
- **Full-suite regression sweep**: `python3 -m pytest tests/ -q` from `harness/` was attempted but exceeded a 10-minute budget (subprocess-heavy suite, ~1176 tests per prior PR bodies) and was killed — inconclusive, not a finding against this PR. Scoped the regression check instead to the new file plus its two explicitly-named companions (reap-mechanism tests), which is the code path this PR actually touches; all green.

No contradiction found. Confirming on the board.
