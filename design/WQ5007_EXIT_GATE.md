# wq#5007 — dual-condition exit gate + circuit breaker

Pattern source: `scratch/research/extract_ralph_claude_code.md` (wq#4975,
frankbria/ralph-claude-code). Patterns ported, no code ported (Ralph is Bash).

## Defect class

A single signal decides a loop's fate in both directions:

| direction | single signal | effect |
|---|---|---|
| false complete | a `result: success` frame, or a commit that names the ticket | exit accepted while the worker itself never said it was finished |
| false kill | `LoopTracker` shared threshold 3 for *same error text* (never reset, even across successful edits) | a TDD loop whose test fails three times with the same tail is SIGTERMed mid-fix |
| blind retry | no memory across loops | a ticket requeued into the same wall is re-run until the dispatcher breaker trips fleet-wide (2026-08-29, 8/9) |

wq#4707's 73 `post_result_stuck` kills were fixed at their own root (PR #2514, background
grace). This change removes the class behind them: **no exit and no kill on one signal**.

## What a loop is

One child invocation = one `do_attempt` leg (fresh spawn, steer / tool-ceiling /
exit-nudge resume, attempt+1 resume). Same unit as Ralph's one `claude` call.

## 1. Exit gate — BOTH or continue

    exit  <=>  completion_indicators >= 2  AND  EXIT_SIGNAL: true

Indicators are harness-observed facts, never prose: `clean_result` (classifier SUCCESS),
`ticket_done` (wq status), `landed_commit` (check_artifacts_done sha), `closed_sentinel`
(TICKET_CLOSED last line). `EXIT_SIGNAL` is the last line-anchored
`EXIT_SIGNAL: true|false` in the final message; missing reads as false.

Enforced in two places:

- **Stop hook** (`hooks/stop_gate.py::_exit_gate_block`) — in-session, at the moment the
  worker tries to stop. Shares the 3-block cap; steps aside for BLOCKED / CLAIM_ERROR and
  for a ticket that already left the worker's hands. Fail-open (convergence aid).
- **fworker** (`do_attempt`, after verify_done/check_artifacts_done) — authoritative. A
  not-accepted exit gets ONE corrective resume in the same attempt number (wq#2138
  precedent), then is scored `FALSE_COMPLETE` and takes the existing resume ladder.

Armed only for sessions whose prompt carried the contract (`EXIT_SIGNAL` in the rendered
prompt, i.e. `prompts/worker.md`); fire / rotation / local-rung prompts and the aider
runtime are recorded (`attempt-N.json: exit_gate`) but not gated.

Deliberate limits, stated so nobody has to rediscover them:

- `killed_reason == ticket_done` is exempt: the harness stopped that session itself.
- **Safety valve** (Ralph v0.10.1 force-exits on repeated completion indicators without
  the signal): signal still **missing** after the nudge, ticket status `done`, >= 2
  indicators -> the loop ends as `DEGRADED_SUCCESS` (`exit_gate.safety_valve: true` in
  the attempt record). Never plain SUCCESS, never on an explicit `false`. Discarding
  verified, landed work over a formatting omission is the false-kill class this ticket
  exists to remove.
- Signal missing and the ticket NOT done: `FALSE_COMPLETE`, existing resume ladder.
- Signal explicitly **false** on a ticket that is not `done`: a commit naming the ticket
  no longer closes it over the worker's objection (new, stricter than before).

## 2. Circuit breaker — per ticket, persisted (`_runs/wq<id>/circuit-breaker.json`)

    CLOSED --(no progress 2)--> HALF_OPEN --(no progress 3)--> OPEN
    OPEN --(30 min)--> HALF_OPEN --probe: progress--> CLOSED / no progress--> OPEN

| trigger | threshold |
|---|---|
| no progress | 3 consecutive loops (worktree fingerprint unchanged, no successful Edit/Write, ticket record unchanged) |
| same error | 5 consecutive loops with the same normalised last tool error |
| output decline | > 70 % fewer output chars than the previous loop, on a loop with no progress |

A loop that ended on a question or parked on a hold holds the counters steady. Progress
that cannot be measured is `None` and never counted. OPEN refuses the next loop before
the claim (`cmd_run`) and before any spawn (`do_attempt`): note + release + soft hold for
the cooldown, exit `DEFERRED` (which does not kick a refill, so no hot loop). A
grant-approved resume is exempt — Frank's approval is spent on exactly that spawn.

In-session, `CircuitLoopTracker` wraps `action_gate.LoopTracker` unmodified: same-error
kill needs 5, the no-progress triggers stay at 3, a permission refusal keeps 3
(PERMISSION_LOOP, wq#1107).

## 3. Hourly call limit

`_runs/.exit-gate-calls.json`, YYYYMMDDHH stamp, flock. 100 loops/h fleet-wide, 12/h per
ticket (`FOREMAN_MAX_CALLS_PER_HOUR`, `FOREMAN_MAX_TICKET_CALLS_PER_HOUR`; 0 disables).
Over the limit = same DEFERRED path. The governor still owns token budget; this bounds
spawn count.

## 4. Session expiry

A session this ticket first saw more than 24 h ago is not resumed; the loop restarts on a
fresh session in the same worktree with prior-attempt notes. Grant/park resumes are
exempt (they have their own lost-session fallback, wq#4765).

## 5. Git backup per loop

Before every loop: `refs/foreman-backup/wq<id>/<epoch>-a<N>`, a commit of the worktree
(tracked changes + untracked files up to 5 MiB each / 100 MiB total, .gitignore honoured)
built through a throwaway index — working tree and real index untouched, nothing pushed.
Large untracked files are left out so an RE worktree's dumps are never hashed into the
object store. Last 10 kept.
`python3 harness/exit_gate.py backups <worktree> <id>` / `rollback <worktree> <ref>`.

## Rollback

`FOREMAN_EXIT_GATE=shadow` records verdicts without gating; `=off` is the pre-wq#5007
harness byte for byte in behaviour (no breaker file, no backup, bare LoopTracker). Every
glue helper in fworker fails open with a WARN line.

## Tests

`tests/test_wq5007_exit_gate.py` (unit: full 4x3 decision table exhaustively, breaker
transitions, thresholds at the boundary, wrapper, limits, expiry, backup/rollback, Stop
hook) and `tests/test_wq5007_exit_gate_integration.py` (real fworker dispatch path with
fake_claude: one-loop exit, nudge costs no attempt, never-signalled exit is not SUCCESS,
shadow/off, OPEN circuit refuses pre-claim then half-open probe closes, hourly limit).
