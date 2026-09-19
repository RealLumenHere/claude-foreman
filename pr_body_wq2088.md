## What

Each dispatcher pass now gates every candidate once, then admits **local-pool tickets first** and cloud tickets second, with stops scoped to their own pool. Local tickets no longer wait on cloud budget, a cloud stop, or the cloud spawn cap. Implements the design in `research/DISPATCH_STALL_RCA.md` §4.3.

## Why

`dispatchable N, spawned 0` persisted after the stale-lock and burn-rate point-fixes because the coupling was structural: one candidate loop, a pass-level `break` on cloud conditions (`no_slots`, burn-rate, tight spawn cap), one shared spawn cap, and one `pass_end` counter pair that could not show a local stall behind a healthy cloud pool.

One slock and one daemon is a deliberate choice. RCA §4.2 option A (two processes) races on `dispatcher/state.json` (lane_counts, spawn_ledger) with no cross-process lock. The slock only guards concurrent passes; both partitions run inside the one pass, so it no longer serializes local behind cloud.

## How

- `dispatcher/foreman_dispatcher.py`: `_do_pass` is split into `_gate_candidate` (stages 3-7 + model resolution), `_admit_local`, `_admit_cloud`, `_spawn`, and `_run_partition`, sharing a `_PassCtx`.
- Local answers only to `local_ceiling` and the new `local_max_spawns_per_pass` (default `local_ceiling`). No governor, burn-rate, pacing or `cloud_ceiling`. PSI stays pass-wide, since it measures the box.
- A cloud `no_slots`, spawn cap or the new soft `pass_deadline_s` (240) ends the cloud partition only.
- Lane `max_per_day` is re-checked at admission. The gate now runs before any spawn, so without this one pass could overshoot the cap.
- `list_running_units()` is read once per pass and after each spawn, not per candidate. Unit-to-local-ness is cached per pass.
- `pass_end` gains `dispatchable_` and `spawned_` for `local`/`cloud`, `dispatchable_unpooled`, `local_slots_full`, `stopped_{local,cloud}_reason`.
- `dispatcher/dispatch_health.py`: judges each pool on those fields. A full local lane counts as healthy, like cloud `no_slots`. Older `pass_end` lines classify as before.
- `dispatcher/config.json`: `local_max_spawns_per_pass: 12`, `pass_deadline_s: 240`. `dispatcher/DESIGN.md` §7 documents the partitions.

## Testing

- 6 new tests in `dispatcher/tests/test_dispatcher.py`: local runs before a cloud stop, local saturates past the cloud spawn cap, cloud gates never touch local, split `pass_end` counters, lane cap across partitions, per-pool health.
- `test_dispatcher.py` + `test_dispatch_health_cause.py`: 133 passed, the 129 pre-existing tests unchanged. The rest of `dispatcher/tests/`: 237 passed.
- Not exercised here: a live pass against the Macs. After merge, pin local tickets, run a pass, and confirm `spawned_local > 0` in `pass_end`.

Most open local-pinned tickets are still structurally undispatchable (RCA layer 5: needs_unresolved, `auto` lane, runaway_stuck). This PR removes the dispatcher-side blockers only.

## Risk / rollback

The largest diff is the `_do_pass` restructure, mostly moved code. Gate decisions are computed once per pass instead of per candidate, so `already_running` sees pass-start units. Rollback is a plain revert. The new config keys are optional, with defaults.

Refs: wq#2088

🤖 Generated with [Claude Code](https://claude.com/claude-code)
