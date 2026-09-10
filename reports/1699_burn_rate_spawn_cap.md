# wq#1699: tight-mode spawn cap scales with budget, tight_pri uses epri

Status: design + implementation record. Supersedes the unlanded wq#1669 PR #420 (its commit is folded in, minus the live-assign pressure wiring).

Refs: wq#1699 (this), wq#1669 (open PR #420, blocked FALSE_COMPLETE, commit 9ad7f6d), research/DISPATCH_GOVERNOR_WALKTHROUGH.md sec 3/5.

## Root cause (evidence grade A: code read + dispatcher.log sec 5 + live state.json)
1. router.governor_pressure returns tight on (remaining-floor)<15 hardcoded OR API severity warning|critical. Floors are 0, so the API label alone flips every pass tight.
2. _do_pass treats tight as binary: per-pass cap -> tight_mode.max_spawns_per_pass, pri>min_pri skipped. No input about how much budget is left or how soon it resets.
3. tight_pri compares cand["pri"] while the scan sorted on cand["epri"] (wq#661 aging). An aged p2->ep1 sorts first and is refused.

## Class
A throttle driven by a label (tight / severity) instead of the quantity it stands for (budget remaining per hour to reset). Other members: pacing.py weekly projection (already rate-based, policy=off, untouched); PSI tight (memory, not budget: binary clamp is correct there, keeps it but fixes epri).

## Existing work
PR #420 (wq#1669) has a pure burn_rate.py controller + router soft_margin_pct from floors.json + severity advisory on floor=0 pools + pressure= wiring into router.assign. It applies clean on main (no commits on touched files since merge-base d45b2c3). Build on it, do not redo it.

## Review findings on 9ad7f6d (fixed here)
- F1 severity: warning and critical both advisory on floor=0 pools. Ticket: warning != critical. Critical (API ~>90% used) must engage the controller; warning stays advisory.
- F2 controller defaults are unmeasured guesses: session 5.0 %/worker-h, weekly 0.5. Measured tonight from journal worker-hours vs state.json percent: session 07:40-10:39Z 0->23% over 18.55 worker-h = 1.24; weekly 07:00-10:39Z 83->89% over 19.48 worker-h = 0.31 (both include Frank's own claude.ai use, so conservative). session 5.0 clamps a 20%-left/4h session to 1 concurrent (0 spawns with 3 running) - the exact wrong clamp. New defaults session 1.5, weekly 0.35 (measured, rounded up). Continuous measurement -> new ticket (observed_burn.json writer).
- F3 cap is sticky pass-level, computed from pass-start unit count, halts with stage=spawn_cap -> a budget-full pass with spawned=0 reads as a stall to dispatch_health. Controller output is a concurrency ceiling, so enforce it at stage 9 against the live unit list the ceiling check already reads, halt as concurrency/no_slots (health = healthy, same as ceiling full). min_allowed=1 means a no_slots halt always has >=1 worker running.
- F4 fable pool ignored by the cap (PASS_POOLS only). A fable model's pools include fable_weekly_scoped (router rule: "fable" in model). Pass pools reached -> halt; only the fable pool reached -> skip that candidate (burn_rate_cap) and keep scanning so non-fable tickets still spawn.
- F5 pressure= wired into spawner.dispatch_one's LIVE router.assign: select() under tight writes a downgraded effort onto the ticket, and a written tuple wins forever (walkthrough sec 3). A transient pressure reading becomes a permanent quality downgrade. Out of wq#1699 scope and unsafe as-is: dropped from this change. candidate_model dry-run wiring dropped with it (keeps candidate model == spawned model). Follow-up ticket for the downgrade decision.
- F6 tight_pri epri: not addressed in 9ad7f6d. Fix both binary paths (PSI + no-data fallback).

## Chosen behavior (as built)
router.governor_pressure(model), per pool the model draws on:
  remaining < floor -> deny; remaining-floor < soft_margin (floors.json soft_margin_pct, default 15) -> tight;
  severity critical -> tight; severity warning -> tight only if floor > 0.
floors.json gains soft_margin_pct=0 (Frank's zeroed floors now zero the margin too).
_do_pass stage 8, pressure tight:
  allowed = burn_rate.allowed_concurrent over pools_for_model(model) (session+weekly_all, +fable_weekly_scoped for fable models), clamped [min_allowed=1, cloud_ceiling].
  allowed None (no percent/resets_at) -> old binary tight clamp, tight_pri on epri.
  else no tight flag, no pri filter; burn_rate event once per model.
stage 9 (same live unit read as cloud_ceiling): cloud units >= allowed ->
  model on pass pools only -> halt concurrency/no_slots (healthy to dispatch_health);
  fable model -> skip budget/burn_rate_cap and keep scanning (its tightness can come from the fable pool alone, so it must never halt tickets on unflagged pools).
PSI tight: binary clamp kept, gate on epri. Pacing hook gets epri (same pri-vs-epri class).
pass_end gains burn_ceiling {pools: allowed}.
Dropped from #420: pressure= into candidate_model / spawner.dispatch_one (F5).

## Tests
New/changed, all red on origin/main except the fallback case: sec5 zero-floors warning (no throttle), sec5 zero-floors critical (scaled ceiling, p2 spawns), burn ceiling replaces binary clamp, scarce weekly -> concurrency no_slots, full ceiling with zero spawns classified healthy, fable pool skips fable only, fable-tight never halts unflagged pools, tight_pri on epri (fallback + PSI), router graded severity, burn_rate math on measured defaults, pools_for_model, mid-session not clamped to one.
Suites: dispatcher/tests, orchestrator/tests, harness test_spawner/test_concurrency, governor. 11 failures in test_self_heal/test_dashboard_data/test_fire_drill are identical on origin/main 31bb097 (live wq#1676 liveness on-ramp gate refuses fixture add/block) -> wq#1721.

## Follow-ups filed
- wq#1721 foreman fixtures refused by the wq#1676 on-ramp gate (deps 1676).
- wq#1722 observed_burn.json writer (rolling measured per-worker burn).
- wq#1723 router effort-first downgrade on the dispatch path: per-spawn override without stamping the ticket, or delete.

## Measurement method (reproducible)
`sudo journalctl --since '2026-09-10 05:00 UTC' --until '2026-09-10 10:45 UTC' -o short-unix | grep lumen-job-fworker-wq` -> per unit start = epoch in the unit name, end = last journal line; worker-hours = overlap with the window. Pool percent from governor state.json (07:00Z values quoted in walkthrough sec 4, 10:39Z read live). Session window assumed to start at 0% at its 07:40Z reset.

## Rollback
Revert the squash commit on foreman main (config read from HEAD, code from primary tree after ff). No DB/state migration.
