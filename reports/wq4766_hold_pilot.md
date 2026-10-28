# wq#4766 — B1-T5 Pilot-1 live run: hold_gate on the verify-probe lane

Design: `foreman/docs/agent-authority-research/DESIGN.md` §2.1, §4. This is the **day-0
baseline**, not the week-4 close-out: the change below starts the soak clock; the metrics
DESIGN.md §4 asks for (holds, approve/deny counts, hold-to-approve latency, non-holdable-class
holds, deny rate, Squidbrake side-by-side) need real verify-probe-lane traffic accumulated over
the following 7 days, which a single worker session cannot fabricate or compress. Same
time-gate shape as wq#4688's soak block (`wq show 4688`, "earliest recheck 2026-11-03").

## What changed (the live wiring)

`harness/permission-profiles/verify-probe.json:144-151` adds a third `PreToolUse` hook entry —
matcher `Bash|Read|Edit|Write` (same matcher guard.sh uses), command
`python3 /home/lumen/foreman/harness/hold_gate.py` — alongside the existing guard.sh (`:125-133`)
and action_gate.py (`:134-142`) entries. `harness/worker-settings.json` is untouched, so every
other lane still sees hold_gate.py absent; only verify-probe-archetype sessions get it. This
matches `hold_gate.py`'s own header comment ("NOT wired into worker-settings.json yet (B1-T5)")
and ticket wq#4760's done-notes ("not wired into worker-settings.json (B1-T5)").

`hold_gate.py` itself is unmodified — landed by wq#4760 (merged 971a628b), covered by its own
16 tests (`harness/tests/test_hold_gate.py`, re-run clean on this branch: 29 passed, 1 pre-existing
unrelated failure in `test_agents_spec.py::test_local_allowed_archetypes_match_pre_wq1301_constant`
— a `probe-build` archetype config-drift failure already flagged in wq#4762's done notes, not
touched by this diff).

## Smoke-test (post-wiring, not pilot traffic)

Confirmed the hook fires on the new wiring by piping a PreToolUse envelope straight at
`harness/hold_gate.py` for a `Read` call: it returned a valid `permissionDecision: "deny"`
(guard.sh's own lower-layer denial — a missing file — not a defer), proving the hook actually
executes in this profile and the §2.2 order-independence pre-check (deny beats defer) holds
end-to-end post-wiring. This single line landed in
`/home/lumen/foreman/events/hold_gate.jsonl` (`hold_gate_lower_deny`, ts `2026-10-28T15:01:18Z`)
and is **not** counted in the soak numbers below — it predates any real verify-probe session
and used a synthetic env, so it never reached the grant-filing step.

## Day-0 baseline (2026-10-28, right after wiring)

- `wq grant list --json` → `[]` (0 rows). No verify-probe session has run against the new
  wiring yet.
- `grant_requests` table: 0 rows, so trivially 0 holds of a non-holdable class (the rollback
  trigger DESIGN.md §4 names) and no deny-rate to compute.

## How to close this out after the soak (recheck on/after 2026-11-04)

1. `/home/lumen/bin/wq grant list --json` (optionally `--state approved|denied|pending|expired`)
   filtered to tickets claimed by verify-probe-archetype sessions in the window; cross-reference
   `class` against DESIGN.md §2.2's closed list `{H1, H2}` — any other value is the "hold of a
   non-holdable class" trip-wire, must be 0 or roll back per §5.
2. Hold count = total rows; approve/deny counts = `state='approved'`/`state='denied'` counts;
   deny rate = denied / (approved + denied), DESIGN.md §4 says >30% means the holdable classes
   are too wide.
3. Hold-to-approve latency = `decided_at - requested_at` for `state='approved'` rows (both
   columns are on the `grant_requests` row, `wq grant show <id> --json`).
4. Corroborate with `/home/lumen/foreman/events/hold_gate.jsonl` (`hold_gate_defer` /
   `hold_gate_allow` / `hold_gate_deny` / `hold_gate_lower_deny` events) for anything that never
   reached a `grant_requests` row (e.g. `hold_gate_lower_deny` — guard/action_gate already denied
   it, so no hold was ever filed).
5. Side-by-side with Squidbrake (wq#4750, `foreman/docs/hardware-re-safety/squidbrake/`): as of
   this writing Squidbrake's own one-week observe run has not produced a live log either — its
   done notes scope that soak + the observe→review decision to "an interactive /re operator task
   (headless worker out of scope by design)" and it ships only
   `docs/hardware-re-safety/squidbrake/events.sample.jsonl`. Pull whatever real Squidbrake log
   exists by the recheck date; if none exists yet, the comparison stays structural (below).
6. Append the numbers to this file and decide keep/rollback per DESIGN.md §4/§5.

## Structural side-by-side (no live counts on either side yet)

| | Pilot 1 — `hold_gate.py` (this ticket) | Squidbrake observe-mode (wq#4750) |
|---|---|---|
| audience | headless fworker, verify-probe lane only | interactive `/re` sessions with a human approver present |
| hold mechanism | native Claude CLI `PreToolUse` → `defer`, resumed via `wq grant` + fworker's `tool_deferred` handling (wq#4765) — no new process | a gateway process sits in front of every tool call |
| holdable classes | H1 path-read, H2 repo-read — closed list, DESIGN.md §2.2 | hardware MCP tool calls / CLI flash-erase-fuse-voltage-clock verbs (`rules.yaml`) |
| cost | $0 — Apache-2.0 harness code already landed (wq#4760/4761/4765) | free for one person; phone/Slack/Teams/email approval and audit export are Team-plan (paid) |
| scope boundary | excludes writes, credential paths, anything root, anything `never.*` | excludes headless fworkers entirely — "a hold would deadlock" (README.md:16-19) |
| live data as of 2026-10-28 | 0 real holds (wiring just landed) | 0 real holds (sample log only) |

## Rollback

Remove the `hold_gate.py` entry at `harness/permission-profiles/verify-probe.json:144-151` (the
third `PreToolUse` array element, command `python3 /home/lumen/foreman/harness/hold_gate.py`).
No other file changes this ticket made. Any ticket parked `awaiting_grant` at rollback time gets
`wq release`d (DESIGN.md §5). `lumen-run --bind-ro` becomes unused again but is left in place
(it is wq#4765's deliverable, not this ticket's).
