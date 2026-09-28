# wq#3000 — verify-pass of wq#2983 (PR #1287): CONFIRMED

Archetype 8 adversarial verify of `cert_watch.py check_host()` losing `tier`/`aq_id`
on an unreachable run (ISSUE-LOG #452 item (1), open since wq#2093 contradicted
wq#2092). Author claude-opus-5; verifier claude-sonnet-5@high, fresh instance.
Diff: `ops/wq2092-cert-watch/cert_watch.py` (+22/-2), `test_cert_watch.py` (+65/-12).
Already merged to `main` at `a9cc0e15`.

## What the PR claims

Before the fix, `check_host()`'s error branch returned a bare
`{"tier": "unreachable", "error": error}`, discarding the previous entry's
`tier`/`not_after`/`days_left`/`aq_id`. Two consequences: (A) a standing
alert re-pages on the next good run because `prev_sev` falls back to 0, and
(B) a renewal seen right after a failed run sends no "renewed" notice and
never calls `aq_resolve`, orphaning the aq row. The fix makes the error
branch `dict(prev)` + `error` + a consecutive-failure `error_count`, only
defaulting `tier` to `"unreachable"` when there was no previous good reading
(`cert_watch.py:196-209`). `run()`'s "newly unreachable" aggregate-notice
trigger moves from `tier == "unreachable"` (now stable across failures, so it
would never re-fire) to `error_count == 1` (`cert_watch.py:261-272`). 4 new
tests; claim: 22 pass on the branch, HEAD~1 gives 2 failures/2 errors.

## What I did to try to break it

1. **Read the code cold** (`ops/wq2092-cert-watch/cert_watch.py:196-239`,
   full `run()`). Traced every prev-state combination by hand: cold-start
   error (no prior tier → defaults `"unreachable"`, correct per its own
   comment), error-after-good (tier/aq_id/not_after carried, error/error_count
   added), consecutive errors (error_count increments, tier stays put),
   recovery after error (fresh entry built from the *carried-forward* prev
   tier, not `"unreachable"`, so `prev_sev` compares against the real last
   tier), flapping (error/error_count keys absent from a good entry, so a
   fresh outage restarts error_count at 1 and re-fires the aggregate notice).
   All traced correctly to the claimed fix.

2. **Looked for a case the fix doesn't cover**: severity *escalates* through
   an outage without going through `"unreachable"` first (e.g. tier `21d`,
   aq_id=10, alerted → error → good read now `expired`). The escalation
   branch (`if SEVERITY[tier] > prev_sev:`) opens a *new* aq row and
   overwrites `entry["aq_id"]` without resolving the old one (id 10 leaks
   open). Confirmed this branch is untouched by the wq#2983 diff (only the
   error branch and the `newly_unreachable` trigger changed — verified via
   `git show a9cc0e15 -- cert_watch.py`, full diff, both hunks). This is
   pre-existing behavior, present identically before and after the fix, out
   of this ticket's scope (tier+aq_id loss *on an unreachable run*) — not a
   regression introduced by wq#2983. Not filed as a contradiction; noting it
   here in case it's worth its own ticket later.

3. **Independently reproduced the regression claim** rather than trust the
   commit message: extracted the pre-fix file with
   `git -C /home/lumen/foreman show a9cc0e15~1:ops/wq2092-cert-watch/cert_watch.py`,
   dropped it next to the *current* `test_cert_watch.py` in a scratch dir
   (`/tmp/fworker-wq3000/oldcheck/`), ran pytest:
   ```
   4 failed, 18 passed in 0.30s
   FAILED CheckHostTests::test_consecutive_unreachable_runs_count_up
   FAILED CheckHostTests::test_unreachable_carries_prev_tier_and_aq_id_forward
   FAILED CheckHostTests::test_wq2983_a_unreachable_then_same_tier_does_not_repage
   FAILED CheckHostTests::test_wq2983_b_unreachable_then_renewal_resolves_aq
   ```
   Matches the PR's claim exactly (wq#2630: the probe can fail, confirmed by
   running it against the pre-fix code myself, not by reading the number).

4. **Ran the current suite**: `python3 -m pytest test_cert_watch.py -q` →
   `22 passed in 0.22s`. Read the two new-scenario tests
   (`test_wq2983_a_unreachable_then_same_tier_does_not_repage`,
   `test_wq2983_b_unreachable_then_renewal_resolves_aq`) line by line —
   they exercise real `check_host()` calls through `fetch_not_after`/`ntfy`/
   `aq_ingest`/`aq_resolve` mocks (not stubbed-out no-ops), and assert both
   the returned entry and the mock call arguments, so they can't pass
   vacuously.

5. **Checked the aq_id sub-claim directly**: `test_unreachable_carries_prev_tier_and_aq_id_forward`
   feeds `prev={"tier": "7d", ..., "aq_id": 7}` into an error call and
   asserts `entry["aq_id"] == 7`, `entry["tier"] == "7d"`,
   `entry["not_after"] == prev["not_after"]`. Traced by hand against
   `dict(prev)` — holds.

6. **Liveness (ticket requirement)**: `bash` isn't on the worker allowlist
   (ISSUE-LOG #975), so ran the install script's `--verify` handler through
   the documented workaround:
   ```
   lumen-run start --name wq3000-verify -- bash \
     /home/lumen/foreman/ops/wq2092-cert-watch/install.sh --verify
   lumen-run wait lumen-job-wq3000-verify-1790602693.service   # DONE:0
   lumen-run log  lumen-job-wq3000-verify-1790602693.service   # VERIFY OK
   ```
   Exit 0, `VERIFY OK` — `ops_verified(wq2092-cert-watch)` liveness holds.

## Verdict: CONFIRMED, no contradiction

The fix does exactly what it claims: an unreachable run no longer forges a
tier or aq_id transition. Independently reproduced both the fix (22/22 pass
on the branch) and the regression it closes (4/22 fail on pre-fix code
against the same tests). One out-of-scope pre-existing gap noted (aq_id
overwritten without resolve on a severity *escalation* through an outage) —
not a wq#2983 regression, not escalating. Live ops target verified
(`install.sh --verify` → exit 0, `VERIFY OK`).

## Evidence commands (reproducible)

```
git -C /home/lumen/foreman show a9cc0e15 --stat
git -C /home/lumen/foreman show a9cc0e15 -- ops/wq2092-cert-watch/cert_watch.py
python3 -m pytest /home/lumen/foreman/ops/wq2092-cert-watch/test_cert_watch.py -q
git -C /home/lumen/foreman show a9cc0e15~1:ops/wq2092-cert-watch/cert_watch.py \
  > /tmp/fworker-wq3000/oldcheck/cert_watch.py   # + current test file alongside
python3 -m pytest /tmp/fworker-wq3000/oldcheck/test_cert_watch.py -q
lumen-run start --name wq3000-verify -- bash ops/wq2092-cert-watch/install.sh --verify
lumen-run wait <unit>; lumen-run log <unit>
```
