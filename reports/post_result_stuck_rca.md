# wq#4707 — RCA: `post_result_stuck` kills workers waiting on their own background task

Date: 2026-10-27 · worker: fworker-wq4707 · filed by orch-burndown-3 (2026-10-26 blocked burn-down)

## Verdict

Hypothesis CONFIRMED, and a second defect found alongside it. Of every FALSE_COMPLETE
in the last 30 days, **58% (60 of 103) were killed by the post-result grace**, not by
anything the worker did wrong. 36 of those 60 ended on an assistant tail that says the
worker was waiting for a background task it had started.

## Evidence

Population: `attempt_end` records in `/home/lumen/foreman/_runs/wq*/episode.jsonl`
(1957 attempts in the 30-day window).

| measure | value |
| --- | --- |
| attempts killed `post_result_stuck` | 73 (60 FALSE_COMPLETE, 12 SUCCESS, 1 DEGRADED_SUCCESS) |
| FALSE_COMPLETE in the 30-day window | 103 — of which 60 (58%) were `post_result_stuck` |
| those 60 whose assistant tail names a background task / notification | 36 |
| the ~25s resumed-attempt signature (<60s, >1 turn) | 29 |
| wall-clock discarded inside the 60 attempts | 5.1h, 2776 model turns (median 140s, worst `wq4634a1` 1442s/112 turns) |
| follow-on attempts those 60 forced | 3.2h across 39 tickets |

Five transcripts read (assistant tails, from the episode records — the CLI's own
transcripts under `~/.claude/projects` are a denied path for workers, wq#2546):

- **wq4686 a1** (227 turns, 1301s) — result frame: *"Waiting for the dispatcher test
  suite (background task `bom0i2gan`) to finish before finalizing the ticket."*
  PR #2465 open, 7 modified files in the worktree.
- **wq4634 a1** (112 turns, 1442s) — *"PR #2465 is open. Waiting on the full harness
  suite verdict before closing the ticket."*
- **wq4644 a1** (63 turns, 566s) — *"I'll wait for the background pytest run to notify
  completion rather than poll further."* Verification already done, report unwritten.
- **wq4239 a1** (71 turns, 413s) — `lumen-pr land` **auto-moved to the background by
  the CLI** on its tool timeout; *"I'll get a notification when it completes"*. The
  agent never asked for a background task — the CLI made one.
- **wq3801 a2** (42 turns, 373s) — a 60-100 min benchmark started in the background
  while the deliverable was drafted.

## Root cause

`harness/fworker.py:run_child()` §C9 SUCCESS-BUT-STUCK. Two defects:

**A — the grace ignores outstanding background work.** `POST_RESULT_GRACE_S` (20s) is
premised on "once a terminal result has landed, no more real work is coming." That is
false when the agent ended its turn with a background task running: the CLI keeps the
session alive precisely so it can re-invoke the agent with the completion notification.
A test suite takes minutes; the grace gave it 20 seconds, SIGTERM'd the session, and the
notification was delivered to a dead process. `reconcile()` then saw a ticket that was
not `done` and scored the attempt FALSE_COMPLETE — with the PR open and the diff on disk.

**B — `result_seen_at` was set but never cleared.** The countdown opened by the *first*
result frame kept running across every later frame, so a session that resumed real work
was killed 20s after that stale result no matter what it was doing. This is the
attempt-2 signature the ticket noticed: 29 attempts under 60s with more than one turn
(`wq4686a2` 4 turns/27s, `wq4239a2` 7 turns/24s), each scored FALSE_COMPLETE with an
empty result text, each one a live worker cut off mid-investigation.

## Fix (this PR)

1. `BackgroundTaskWatch` (`harness/fworker.py`) tracks outstanding background work from
   the stream: `run_in_background` on a tool_use, any `Agent`/`Task` call that did not
   opt out, and the CLI's own auto-background of an over-long Bash call (wq4239's case,
   visible only in the tool_result text). Completions come from the notification text.
   Generous on starts, strict on completions: over-counting only stretches a window for
   a process that is still alive, under-counting is what killed 60 workers.
2. While a task is outstanding the grace becomes `POST_RESULT_BG_GRACE_S`
   (`FOREMAN_POST_RESULT_BG_GRACE_S`, default 900s, `--post-result-bg-grace`). Bounded
   on purpose: a task that never reports still ends the session, it does not hang a lane.
   Wall-clock remains the outer ceiling. Costs nothing on the happy path — a finished
   child closes stdout and the loop breaks on eof long before either window.
3. Real turn activity (`assistant`/`user` frame) after a result clears `result_seen_at`,
   so the grace rearms from the next result instead of a stale one (defect B).
4. `state.bg_tasks_started` / `bg_tasks_pending` ride the `attempt_end` episode record,
   so the next burn-down can separate "killed waiting on its own test run" from a
   genuine post-result hang without grepping assistant tails for English phrases.
5. `harness/prompts/worker.md`: long commands run in the foreground with a `timeout`, or
   detached under `watchdog` and **polled on the worker's own turns** — never parked on
   "I'll wait for the notification". The 900s window is a safety net, not a licence; the
   wq3801 60-100 min benchmark would still exceed it.

## Test to failure

`harness/tests/test_wq4707_bg_grace.py` (11 tests) + the committed replay fixture
`harness/tests/fixtures/wq4707_wq4686_a2_frames.jsonl` (the wq4686 attempt-2 shape:
early empty result, four live turns, a backgrounded suite, the notification, the land).

Same fixture replayed through both trees (`/tmp/fworker-wq4707/prefix_probe.py`):

| tree | killed_reason | exit | elapsed | reached the land step |
| --- | --- | --- | --- | --- |
| pre-fix (`/home/lumen/foreman/harness`) | `post_result_stuck` | 143 | 1.4s | **no** |
| fixed, default 900s grace | none | 0 | 4.3s | yes |
| fixed, injected 8s grace | none | 0 | 4.3s | yes |

The suite also pins the inverse cases so the fix cannot become a hang: a stuck process
with *no* background task is still killed on the 20s grace (§C9 regression guard,
asserted under 6s), a completed task releases the long window, and a task that never
reports is killed at the long grace, not never. Two probes are deliberately outside the
population the fix was designed around (wq#2630): the `Agent` tool, which backgrounds
with no flag in the call at all, and wq4239's CLI auto-background, which appears only in
tool_result prose — that second probe **failed on first run** and forced the start
detection to widen ("moved **to** the background", not just "in the background").

## Re-evaluation of the last 30 days

The 39 tickets whose attempts were killed this way: **36 are `done`** (the bug cost a
redispatch each, not the work), 2 `rejected` (wq1921, wq1952), 1 `pending_review`
(wq3457). Nothing needs reopening on correctness grounds; the cost was 5.1h of discarded
model work plus 3.2h of forced retries, and 60 attempts of noise in the FALSE_COMPLETE
rate that made the burn-down read worse than reality. The `bg_tasks_*` fields added here
make the next pass measurable rather than phrase-matched.

Related: wq#4634 (re-issue ceiling-killed Bash via watchdog — the same "long command"
problem one layer down), wq#4629.
