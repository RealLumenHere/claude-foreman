# harness — T2a worker harness

wq#625, BUILD_PLAN.md §T2a. Implements
[design/T2A_WORKER_HARNESS_DESIGN.md](../design/T2A_WORKER_HARNESS_DESIGN.md), Frank-approved
via PR#2. One `fworker` supervisor process per ticket, spawning exactly one `claude -p`
child at a time on the shared login (multi-worker auth is T2b, gated on T2b-gate).

## Layout

    fworker.py              supervisor: spawn + parse + classify + wq lifecycle
    classifier.py           RELIABILITY_SPEC.md §C/§D verdict engine (SUCCESS/TURNS/
                             LAUNCH_FAILURE/LIMIT/KILLED/OOM/INDETERMINATE/AUTH/
                             DEGRADED_SUCCESS/FALSE_COMPLETE + REFUSED/BUDGET/CRASH/HANG)
    reaper.py                independent wq lease reaper (§E, wq#647) — `wq reap` +
                             an aq row per reap, decoupled from any worker's own exit path
    worker-settings.json    settings pack — replaces worker-settings-interim.json
    guard.sh                PreToolUse fail-closed guard (§5)
    prompts/worker.md       worker prompt template
    tests/                  pytest suite (zero Anthropic quota)

Runtime paths (governor-owned or created on demand, not checked in):
`../events/<worker-id>.jsonl` (lifecycle + blocked events), `../_runs/wq<ID>/`
(attempt-N.json, prior-attempt-notes.md), `/home/lumen/worktrees/wq<ID>-a<N>/`
(per-attempt git worktrees), `../state.json` / `../STOP` (T1 governor).

## Running a ticket

    python3 harness/fworker.py run --ticket <ID> --repo <path> \
      --model claude-sonnet-5 --effort high --archetype standard-engineering

Launch via `lumen-run` so the supervisor itself survives DC-gateway reaping. RELIABILITY_SPEC.md
§E: `--timeout-stop-sec 90` (>=60) so a unit stop/recycle SIGTERMs fworker with enough grace
for its own SIGTERM->SIGKILL child-kill escalation and the CLI's SessionEnd hooks to finish
cleanly instead of racing a hard systemd SIGKILL — `router.py dispatch-cmd` already sets this:

    lumen-run start --name fworker-wq<ID> --timeout-stop-sec 90 -- \
      python3 /home/lumen/foreman/harness/fworker.py run --ticket <ID> --repo <path> \
        --model <id> --effort <name> --archetype <name>

`--model/--effort/--archetype` may be omitted (wq#617): fworker then reads the tuple the
orchestrator wrote onto the ticket (`wq` model/effort/archetype columns) or asks
`../orchestrator/router.py assign` on the spot; explicit flags always win. On SUCCESS
reconcile it calls `../orchestrator/judgment.py verify-pass --for <ID>` (best-effort;
`FOREMAN_NO_VERIFY_PASS=1` disables).

Exit codes: `0` success, `10` claim error, `11` deferred (governor/STOP gate), `12`
blocked (2 failed attempts, ticket wq-blocked), `13` limit (ticket released, resets
outside the resume window).

`--frame-gap-timeout SEC` (§E: T_gap, default/env `FOREMAN_FRAME_GAP_S`, 120-300s range)
overrides the primary stall watchdog; `--post-result-grace SEC` (env
`FOREMAN_POST_RESULT_GRACE_S`) is the much shorter grace after a terminal result frame
before fworker force-kills a stuck-but-already-succeeded child (§C9). Both exist mainly
so tests can run them fast — production defaults are fine left alone.

## Lease reaper (wq#647, §E)

wq's own lease-expiry reclaim only runs lazily, inside a `wq claim` call for the SAME
lane — a dead fworker (host died, OOM, DC-gateway reap of something that wasn't under
lumen-run) leaves its claim dangling until someone happens to claim that lane again,
which can be hours (fq#614: three same-day drain-deaths this way). `reaper.py` is an
independent process that sweeps ALL lanes on its own schedule:

    python3 harness/reaper.py sweep            # one pass, all lanes
    lumen-run start --name foreman-reaper -- \
      python3 /home/lumen/foreman/harness/reaper.py loop --interval 120

Every reclaim posts an `aq` row (source `foreman-reaper`) so a dangling claim is never
silent again, then reverts the ticket to `open` via `wq reap` (a new lane-independent
subcommand, same reclaim semantics `wq claim` already uses for its own lane).

## Build-time discovery vs the design (read before touching --max-turns)

CORRECTED (wq#646, RELIABILITY_SPEC.md §A): claude CLI 2.1.233 did **not** drop
`--max-turns` — that earlier belief, from a probe against 2.1.220, was wrong.
`fworker.py` passes `--max-turns`; the CLI errors on ceiling with a real
`result:error_max_turns` frame (PROBE_RESULTS.md live-confirmed), which is the
evidence RELIABILITY_SPEC.md §C2 requires for a TURNS verdict ("never legal without a
result frame"). The supervisor also counts `assistant` stream-json frames itself
(`classifier.RunState.turns`) and SIGTERMs the child on a backstop ceiling a few turns
above `--max-turns` (`fworker.TURN_BACKSTOP_BUFFER`) — defense-in-depth only; if that
backstop has to fire, no result frame arrives and the attempt classifies KILLED, not
TURNS. See the module docstrings in `fworker.py` and `classifier.py`.

`claude agents --json` (confirmed working, no TTY needed) is used only as an advisory
liveness signal during `--resume` reconciliation (design §8) — never the control channel.

## Tests

    cd harness && python3 -m pytest tests/ -v

36 cases: guard.sh corpus (every deny class + quoting/`bash -c` evasions, lifecycle
event logging, fail-closed on malformed/empty/off-host input), the classifier's full
decision table including the turn-ceiling case, settings-pack lint (acceptEdits, no
bypassPermissions anywhere in the harness or in the retrofitted gh-lane-worker,
docker ps/logs allow vs docker-star deny, credential-path denies), the STOP/can-dispatch
governor gate, and four end-to-end fworker runs against a fake `claude` shim
(`tests/fake_claude.py`) covering success + worktree isolation, a denied verb actually
blocked at the hook during a real spawn, a synthetic rejected rate-limit frame driving
the real LIMIT path (release, not block), and resume-after-LIMIT reusing the worktree.

`probe_wq625.py` (`/home/lumen/bin/probe_wq625.py`) wraps this suite plus the file-layout
and gh-lane-worker-retrofit checks into the ticket's probe gate; it's the thing CI/a
human runs, not the pytest invocation directly.

## What's deliberately not exercised here (quota-costing, follow-up)

Design §12 item 5/6: one real sonnet-5@low (or haiku) ticket through fworker end to end,
and one gh-lane-worker smoke on a trivial live github-lane ticket. Both cost live Max-plan
quota; this build's probe gate is zero-quota by design (matches the ticket's two named
adversarial criteria — denied-verb-blocked-at-hook, LIMIT!=CRASH — plus the full harness
suite). Schedule the quota-costing smoke off-peak when convenient; nothing in the design
depends on it to land T2a.

## Known gaps vs the full design (explicit, not silent)

- Restart reconciliation (§8) trusts wq status + `_runs/` + an advisory `claude agents
  --json` liveness check; it does not yet re-attach WATCH-ONLY to a live orphaned child
  process by pid/cmdline. A reaped fworker's lease simply expires and the ticket returns
  to the queue loudly (existing wq machinery), which is safe but not the fully-optimized
  path the design describes.
- OTEL env vars are wired into every child's env (§10) but not independently probed here
  beyond the confirmed-live ingest URL already in the design doc; verifying a metric
  actually lands on Bes VictoriaMetrics is part of the quota-costing smoke above.
