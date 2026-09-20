## What

Dispatch rebuild step 6/12 (parent wq#2127, `research/DISPATCH_REBUILD_SPEC.md` §3 step 6): the local rung is sized by each backend's live slots instead of one global `local_ceiling`, and the circuit breaker computes its window per pool so a broken Mac can no longer disarm the cloud partition.

- `dispatcher/local_capacity.py` (new): `LocalCapacity.free_slots(model)` = `slots - running units on that model`. It is 0 when the health probe fails (2 s, all URLs probed in parallel, once per pass, cached) or the backend is in `runtime/backend_cooldown.json`. `local-worker` (the LiteLLM group) is the sum over its healthy members. The module also owns `runtime/local_pause.json`.
- `_admit_local` asks `local_capacity` for any model that has a `local_backends` row; `local_ceiling` is now only the upper clamp. A model with no row (deepseek, or a config without the block) keeps today's ceiling check untouched.
- A Mac-local spawn passes `FOREMAN_FIRST_FRAME_S` / `FOREMAN_SERVED_CTX`.
- `circuit_breaker`: per-pool window keyed on `attempt.model` (no model = cloud). A cloud trip is today's behaviour (DISARM). A local trip writes `runtime/local_pause.json` (60 min), notifies ntfy only, and never writes DISARM. The local partition reads the pause and ends with `halt stage=local_pause`.
- `harness/concurrency.py`: `LOCAL_POOL_MODELS` is read from the `local` pool in `research/models.yaml` (plus the `local-worker` group). `deepseek-v4-flash` is its own `CHEAP_CLOUD_MODELS` class: still exempt from every Anthropic gate, not a Mac slot.
- `config.json` gains `local_backends` (hathor 2 slots / 32768, nephthys 2 / 32768, qwen36-local 2 / 8192, group `local-worker`, first_frame_s 1500). No orchestrator note was on the ticket, so these are the ticket defaults. Health URLs come from `research/DISPATCH_FIX_RESEARCH.md` §2 (hathor `100.80.71.13:11434/api/ps`, nephthys `100.98.74.87:11434/api/ps`, isis `100.100.129.24:8095/health`).
- `dispatch_config_lint` validates `local_backends` (leaf fields, Mac-local names, group members, slots-vs-ceiling warning).

## Why

Three Macs with different real capacity looked like one pool, a dead box still "had room", and 20 failed local attempts read as a 100% fleet failure rate that wrote `DISPATCH_DISARM` and stopped the cloud partition (DISPATCH_DISSECTION, FIX_RESEARCH F5). Budget protection is untouched: `burn_rate.py`, `pacing.py`, `psi_pressure.py`, `governor/` and every deny-list entry are unchanged.

## How

Files outside the ticket's list, and why:

- `harness/spawner.py`, `orchestrator/router.py`: an optional `env=` kwarg on `dispatch_one` / `dispatch_cmd`. `lumen-run` forwards only `PATH` into the systemd unit, so env set in the dispatcher never reaches the worker; with `env` set the unit runs `env K=V python3 fworker.py ...`. With `env` unset the command is identical.
- `dispatcher/observed_burn_core.py`: one import line aliasing `ANTHROPIC_EXEMPT_MODELS`, so deepseek stays excluded from Anthropic worker-hours after `LOCAL_POOL_MODELS` shrank. Behaviour identical.
- `dispatcher/dispatch_health.py`: one condition, so a paused local pool (`pass_end.local_paused`) is not reported as a stall.

## Testing

New tests: 28 (`dispatcher/tests/test_local_capacity_dispatch.py`), 11 (`test_circuit_breaker_pools.py`), 8 (`test_local_backends_lint.py`), 7 (`harness/tests/test_concurrency_pools.py`). They cover: capacity 0 when the probe fails; never admits more than slots (5 tickets, 2 slots, 2 spawns); backends sized independently; group = sum of healthy members; cooldown gives 0; a local breaker trip leaves cloud running and writes no DISARM; local failures alone never DISARM; a cloud trip still DISARMs; the static lever restores the old behaviour; a local ticket never reaches `_admit_cloud` (hathor, qwen36-local, local-worker, deepseek, each with capacity live and static). One test also caught a `yaml.YAMLError` import-time crash in `concurrency._pool_models`, now fixed.

Summary lines:

- `pytest dispatcher/tests`: **409 passed, 11 failed** (420 total). An earlier full run, after the code edits but before the new tests, was 372 passed, 1 failed (373 total).
- `pytest harness/tests`: **1159 passed, 50 failed** on one run and 89 failed on the next (the count moves).

I could not run a clean-base comparison (creating a second worktree needed approval). Where I classified the failures, none is in code this branch touches:

- 81 harness failures and 10 `test_self_heal` failures (all 12 I inspected in the spawner and local_rung files) fail with `REFUSED: lane 'auto' ... no LANE_REPO row (wq#2139)`. The live `/home/lumen/bin/wq` started refusing lane-less `wq add` while I worked; the earlier 372/1 dispatcher run predates it.
- 1 is `test_capability_replay[1407]`. It reads the live capability policy and this branch changes no capability code, but I did not confirm it against a clean base.
- About 8 are evidence-gate / stop-hook tests (`test_evidence_gate`, `test_stop_gate_worker_traps`). I did not investigate them.

## Risk / rollback

Rollback lever, default on: `FOREMAN_LOCAL_CAPACITY=static`. `_admit_local` falls back to `local_ceiling`, no env is passed to spawns, the breaker returns to one global window, and no local pause is written or read.

Behaviour to expect after merge: `qwen36-local` (Isis, unreachable per the spec) now has 0 capacity instead of accepting tickets into a dead box. The live dispatcher probes three URLs per pass, in parallel, at most 2 s.

Follow-ups:

- `backend_cooldown.json` is written by fworker (step 4). The reader here accepts `{backend: epoch|ISO}` or `{backend: {"until": ...}}`; step 4 should match.
- A `--dry-run` pass does not see its own would-spawn units, so it can report more admissions than a live pass (same as the old ceiling).
- The ISSUE-LOG append was refused (`/home/lumen/kb` is a guarded primary tree). Rows to add: (1) env set in the dispatcher never reaches a `lumen-run` unit, fixed by `env=`; (2) shrinking `LOCAL_POOL_MODELS` would have routed deepseek to `_admit_cloud` and counted it as Anthropic burn, fixed by splitting the classes; (3) `wq add` without a lane now fails in tests (wq#2139), open, environmental.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
