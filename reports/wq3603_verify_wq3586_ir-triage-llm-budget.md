# wq#3603 — verify wq#3586 [sentinel] aq/failed-unit: alert source 'failed-unit' has 11

Verifier: claude-sonnet-5@high (fresh instance, build worker, worktree `wq3603-a2`). Author of
wq#3586: claude-opus-5-5. Archetype 8 adversarial verify-pass. Verdict: **CONFIRM**.

## What wq#3586 claimed

sentinel-watch filed aq/failed-unit (11 open rows, 44 total repeats; top row aq#2314 x12
"Failed unit: lumen-ir-triage.service") because deepseek-v4-flash answered 402 on every pass, so
every one of the three X-MASIR roles fell back to cerebras-oss-120b, which on 2026-10-07 was
timing out at 180s per call, retried 3x per role. Three roles at worst case could run ~28 minutes,
past the unit's `TimeoutStartSec=15min`, so systemd killed the run and the unit went failed every
hourly fire. PR#1682 (commit `35241f4870` on main, builds on `96a3e5f2`'s 402-fallback): added a
wall-clock budget (`IR_TRIAGE_LLM_BUDGET_S`, default 600s) shared across all LLM calls in one
pass; each `urlopen` timeout is clipped to whatever budget is left; once spent, the pass is
counted as a model failure (one aq row, exit 0, seen-set not advanced so the next timer fire
retries). Claimed: new test fails against the previously deployed `ir_triage.py`, 23 tests pass,
deployed to `/home/lumen/minerva/ir_triage.py`, 9 aq rows resolved, `sentinel-watch --check
aq/failed-unit` CLEAR, skillhex/reflector rows left open intentionally under other tickets.

## What I checked, independently of the claimed transcript

1. **Read the diff cold** via `git show 35241f4870` (2 files, +39/-2) rather than trusting the PR
   description — avoided the blocked `git remote -v`/cd+VCS/gh-without-slug shapes by reading the
   commit directly from local history and inspecting the current file content with `Read`.

2. **Traced the budget mechanism by hand** against `ops/wq2836-ir-triage/ir_triage.py`:
   `_deadline` is a module-level dict set once via `_deadline.setdefault('t', monotonic()+budget)`
   on the *first* `chat()` call of a pass, so it is shared across all three roles (evidence /
   correlate / plan) within one `run()` — not reset per role. `_chat_one` checks
   `_budget_left() <= 0` before every attempt and clips `timeout=min(LLM_TIMEOUT_S, left)`, so once
   the budget is spent, remaining models/attempts return `''` immediately with **no further HTTP
   calls** (verified by hand-stepping `test_hanging_fallback_stops_at_the_pass_budget`: model `a`
   refuses on 402, model `b` hangs 3x at 180/180/40s exactly exhausting a monkeypatched 400s
   budget — matches the test's own assertions on `fake.now` and `fake.timeouts`).

3. **Re-ran the bundle's own test suite**: `python3 -m pytest ops/wq2836-ir-triage/test_ir_triage.py -q`
   → **23 passed**, 0 failed — matches the claimed count.

4. **Verified the "new test fails on old code" claim independently**, rather than trusting the
   quoted transcript: extracted the pre-image `ir_triage.py` from the parent commit
   (`9cf0c9d4`) via `git show 9cf0c9d4:...` into a scratch dir, paired it with the new
   `test_ir_triage.py`, and ran pytest against that combination. Result: `AttributeError:
   <module 'ir_triage'> has no attribute 'LLM_BUDGET_S'` / `'_deadline'` — the gateway fixture and
   the new hanging-fallback test both fail hard against the unpatched module, confirming the new
   test is not vacuous.

5. **Hunted for the failure mode a synthetic test could miss**: whether 600s is actually enough
   slack under `TimeoutStartSec=900s` once real collection/save overhead is added. Checked the
   live post-deploy systemd run rather than assuming: `journalctl -u lumen-ir-triage.service
   --since "13:55" --until "14:15"` shows a **real reproduction of the exact pre-fix failure
   shape**, live, right after deploy — `14:03:09Z deepseek 402`, `14:06:09Z cerebras timeout
   (attempt 1)`, `14:09:12Z cerebras timeout (attempt 2)` — then the pass continued and **wrote a
   proposal at 14:14:59Z**, for a total of `11min 51.698s` wall clock (journal "Consumed ... wall
   clock time"), comfortably under the 15-minute unit timeout. Before this fix, this identical
   402+timeout pattern is what drove the unit to ~28 minutes and a systemd kill (the root cause
   wq#3586 was filed over). This is the single piece of evidence I trust most: it isn't a mocked
   unit test, it's the actual failing input class hitting the actual deployed code post-fix and
   completing successfully.

6. **Confirmed the fix is actually deployed and the unit is healthy now, not just claimed**:
   `systemctl show lumen-ir-triage.service -p ActiveState -p Result -p ExecMainStatus` →
   `inactive` / `success` / `0`. `systemctl list-timers lumen-ir-triage.timer` → last fire
   `15:03:20Z` (16 min before this check), next in 42 min — i.e. at least one more full cycle ran
   clean after the 14:03 budget-bounded run. `python3 /home/lumen/bin/sentinel-watch --check
   aq/failed-unit` → `CLEAR`, run fresh myself, independent of the author's quoted output.

7. **Checked live aq state for the specific row the ticket names**:
   `python3 /home/lumen/agents/alert-queue/aq.py list open` → aq#2313 (`ntfy:lumen-alerts`,
   "Failed unit: lumen-ir-triage.service") is still open at x12 but its `last_ts` is
   `2026-10-06T22:06:29Z` — unchanged since before the fix, i.e. the unit has not failed again
   since deploy, consistent with the two clean post-deploy cycles above. The two rows still open
   under the `failed-unit` source itself (`1414712` lumen-reflector.service) match the done-notes'
   explicit carve-out (blocked on wq#3410/wq#3476/wq#3477, out of this fix's scope) rather than
   contradicting the fix.

## Where I tried to break it

- Did not trust "23 tests pass" or "new test fails on old code" from the done-notes — reran both
  independently, including reconstructing the pre-image module from git history to get a real
  negative control.
- Looked for the obvious way a shared-deadline design could be wrong: whether the deadline is
  actually read once (good, `setdefault`) vs. reset per role (would silently restore the
  28-minute-worst-case bug) — confirmed by code reading and by the hand-traced test.
- Looked for whether budget exhaustion still burns HTTP calls needlessly once spent (it does not:
  `_chat_one` returns `''` before calling `urlopen` once `left <= 0`).
- Looked for a live counterexample instead of only trusting unit tests: found one for free — the
  very first post-deploy pass reproduced the exact pre-fix failure chain (402 + 2x cerebras
  timeout) and still finished in under 12 minutes, which is the real-world case the fix exists for.
- Checked that `sentinel-watch --check aq/failed-unit` CLEAR isn't just "zero open rows" (it
  isn't — aq#1414712/aq#2313 are still open) so the CLEAR verdict isn't trivially true; it reflects
  the specific repeat/age threshold the sentinel uses, and the rows that remain open are the ones
  the done-notes explicitly scoped out.
- No contradiction found.

## Verdict: CONFIRM

wq#3586's fix is correctly implemented, well-tested (23/23 passing, new test proven non-vacuous
against the pre-image), deployed, and — the strongest evidence — has already handled a live
recurrence of the exact failure pattern it was written for (deepseek 402 + repeated cerebras
timeouts) in 11m51s instead of running past the 15-minute unit timeout. `sentinel-watch --check
aq/failed-unit` is CLEAR, independently re-run, and the unit's two most recent fires both
succeeded (`Result=success`, `ExecMainStatus=0`).
