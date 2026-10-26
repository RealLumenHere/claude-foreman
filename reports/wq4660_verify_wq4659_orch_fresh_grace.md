# wq#4660 — verify-pass of wq#4659 (orch-built tickets race the broker: wq add + immediate build)

**Ticket under verification:** wq#4659 — an orchestrator that files a ticket
with `wq add` and starts building it itself races the dispatcher, which spawns
a duplicate fworker seconds later (wq#4645 -> duplicate PR `lumen/ccc#13`;
wq#4658 -> a 35-minute Sonnet run stopped uncommitted).

**Nominal deliverable, as recorded in wq#4659's own done-notes:** two PRs, not
one:
- `lumen-bin` PR#273 (http://100.121.17.5:3000/lumen/lumen-bin/pulls/273,
  commit `1b2e067`) — `wq add --claim` holds the caller's claim inside the
  INSERT itself, plus the new `created_by`/`released_at` columns.
- `claude-foreman` PR#2474 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2474,
  commit `33320b21` on the repo checked out at `/home/lumen/foreman`) — the
  `OrchFresh` dispatcher gate this ticket's "Verify repo: /home/lumen/foreman"
  points at.

**Metadata note:** wq#4660's own ticket text labels this "PR#273", but PR#273
is the `lumen-bin` half of wq#4659's work, not the foreman-repo dispatcher gate
(that's PR#2474). The ticket's "Verify repo: /home/lumen/foreman" is correct
and unambiguous, so this verify-pass scoped itself to commit `33320b21` (the
actual diff living in that repo) rather than the mislabeled PR number. Flagging
for whoever filed wq#4660 — likely copied the wrong number off wq#4659's
two-PR notes.

**Verdict: CONFIRM** the race-prevention mechanism itself — correct, tested,
no bug found in the dispatcher gate's own logic. **One real defect found** in
an un-diffed sibling file the PR should have touched (dispatch_janitor.py) —
reported separately as a non-blocking follow-up, not a contradiction of
wq#4659's central claim (see below).

## What was verified (commit `33320b21`, 4 files, +146/-2)

Read cold via `git show 33320b21` (dispatcher/foreman_dispatcher.py,
dispatcher/gates.py, dispatcher/tests/test_gates_replay.py,
dispatcher/tests/test_wq4659_orch_fresh.py).

**`orch_fresh_until(t)`** (foreman_dispatcher.py:614-650): returns the grace
deadline (wq timestamp string) or `None`. Traced every branch:
- `grace <= 0` (incl. `FOREMAN_ORCH_GRACE_MIN=0`) -> `None`. Rollback path works.
- `created_by` missing, `None`, `"None"`, or not `orch-`-prefixed -> `None`.
  Confirmed `"orch-"` is a real, live orchestrator-identity convention, not an
  invented one: `qf_dispatch_watcher.sh:7` exports
  `WQ_AGENT=orch-uni-a5f1c4`, and `/home/lumen/kb/ISSUE-LOG.md` has many prior
  real orchestrator session names following the same pattern
  (`orch-opus48-c569a3-5a39b4`, `orch-fleetops-broker`, etc.) — checked via a
  subagent so this wasn't taken on the PR's word.
- `released_at` set to anything but `"None"`/empty -> `None` (released tickets
  never held). Matches the `wq` CLI side: `cmd_release`'s `open` branch stamps
  `released_at=now()` (`/home/lumen/bin/wq:2673-2678`, itself part of wq#4659
  via the companion `lumen-bin` commit `1b2e067`).
- unparseable/missing `created_at` -> `None` (pre-migration row safety).
- window math: string-compared against `_utcnow().strftime(...)`, same
  naive-UTC-string convention already used by `hold_reason`
  (foreman_dispatcher.py:609) and by `wq`'s own `now()` (`FMT =
  "%Y-%m-%d %H:%M:%S"`, `/home/lumen/bin/wq:145`) — not a new, divergent
  pattern, so no tz/format mismatch risk.

**`OrchFresh` gate** (gates.py:354-365): structurally identical to the
existing `Hold` gate (gates.py:339-351) — same `Fail(..., parkable=True,
fingerprint_inputs=(...), recheck_after=...)` shape, correctly wired via
`g.fd.orch_fresh_until(g.t)` (the documented way gates reach dispatcher
helpers without a second module import, per gates.py's own module docstring).
Inserted into `GATES` between `Hold()` and `Deps()`, and `GATE_ORDER` in
`test_gates_replay.py` updated to match — ran the full replay test, passes.

**Checked whether this could re-park an already-dispatched ticket** (i.e.
race the *other* way): no — the dispatcher only ever gates tickets the `wq
list` scan returns, and that scan is `status=open` only
(foreman_dispatcher.py:192 comment + the lane-scan helper). A claimed/running
ticket never re-enters `_gate_candidate`, so `OrchFresh` cannot interrupt live
work.

**Tests run (fresh, this session):**
```
cd /home/lumen/worktrees/wq4660-a1/dispatcher && python3 -m pytest \
  tests/test_wq4659_orch_fresh.py tests/test_gates_replay.py -q
.....................................                                    [100%]
37 passed in 3.50s
```
Full dispatcher suite (`tests/`) also run (background, ~6+ min — a 930+ test
suite); author's own done-notes claim "930 pass, 10 fail all pre-existing on
base".

## Adversarial findings (archetype-8 "try to break it")

1. **`dispatch_janitor.py` SELF_RESOLVING gap (real, not touched by this PR).**
   `OrchFresh` parks tickets under reason `orch_fresh` with the exact same
   self-resolving shape as `held` (clears on its own within
   `FOREMAN_ORCH_GRACE_MIN` or on `wq release` — no human action needed).
   `dispatch_janitor.py:65` defines
   `SELF_RESOLVING = frozenset({"lane_cap", "held", "deps_unmet"})`, which
   `post_digests` (dispatch_janitor.py:314-326) uses to skip filing a daily
   `aq` digest row for causes that clear themselves. `orch_fresh` was never
   added to that set. Confirmed empirically:
   ```
   python3 -c "import dispatch_janitor as dj; print('orch_fresh' in dj.SELF_RESOLVING)"
   False
   ```
   Consequence: any 15-minute janitor pass that catches a ticket parked under
   `orch_fresh` files one `aq` row that day ("N ticket(s) parked —
   orch_fresh"), identical in shape to a stuck-ticket alert — for what is, per
   wq#4659's own instinct candidate (ISSUE-LOG row 1589), the *common* case:
   "one meant for a worker it files plainly." This isn't a rare edge case; it
   is the expected shape of most orchestrator-filed worker tickets that don't
   use `--claim`. `dispatch_janitor.py` has no test file at all, so nothing in
   CI would have caught the omission.

   This does **not** contradict wq#4659's central claim — the race it was
   filed to fix (duplicate fworker spawn) is correctly prevented, tested, and
   independently re-verified above. It's a real but secondary completeness
   gap in a sibling file the PR never touched. Logged to
   `/home/lumen/kb/ISSUE-LOG.md` row 1590 and filed as a non-blocking
   follow-up: **wq#4661** ("[foreman] orch_fresh park missing from
   dispatch_janitor SELF_RESOLVING, noisy daily digest"), depends on wq#4659,
   with a liveness probe (the one-liner above — fails now, must pass after the
   fix). Per this ticket's "do NOT re-implement; verify" instruction, the
   one-line fix (add `"orch_fresh"` to the frozenset) was not applied here.

2. No other integration point was missed: `dispatch_health.py` has no
   per-reason table `orch_fresh` would need registering in (grepped — no
   `held`/`hold` references at all). `gate_store.py`'s park/fingerprint/
   recheck machinery is generic over `reason` and needed no changes.

3. No off-by-one/boundary bug in the grace window: `age_s=599` held,
   `age_s=600` (exactly `FOREMAN_ORCH_GRACE_MIN` minutes) not held — the
   author's own test (`test_window_expires`) already pins this, re-run and
   confirmed above.

## Conclusion

CONFIRM for wq#4659's actual, stated scope: the duplicate-fworker race on an
orchestrator's own just-filed, just-claimed-by-itself ticket is correctly
closed by both halves of the fix (`wq add --claim` + the `OrchFresh` grace
gate), with no correctness bug found in either diff and the shipped tests
re-run clean. No escalation to opus-5@high; no judgment file. One real,
independently-confirmed defect (dispatch_janitor.py's missing
`SELF_RESOLVING` entry, causing daily false-positive digest noise on the
*common* plainly-filed-ticket path, not just the race edge case) was found,
logged (ISSUE-LOG row 1590), and handed off as follow-up wq#4661 rather than
fixed in this verify-pass.
