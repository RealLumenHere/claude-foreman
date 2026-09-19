## What

RCA for the chronic "dispatchable N spawned 0" dispatcher signal and the local batches that do not last the night, plus the one new root cause it found, fixed here.

- `research/DISPATCH_STALL_RCA.md`: verify-first evidence, five layered causes, the local-vs-cloud architecture answer, follow-up actions, and the two ISSUE-LOG rows staged verbatim.
- `self_heal.wq_rows(ids)`: one read-only sqlite read of `attempt,status,lane` for all ids (same `WQ_DB` env `bin/wq` honours), falling back to per-id `wq_show` on any sqlite error.
- `runaway.stuck_ticket_ids()` consumes it instead of shelling out once per `_runs/wq*` dir.

## Why

Findings, in the order they stack:

1. Metric: every "stalled" pass from 09-13 to 09-19 07:00 had zero non-structural skips. `lane_cap` was counted dispatchable until b8a8065. The watchdog was reading a working dispatcher as stalled.
2. Lock: the wq#2080 slock leak is real and fixed, but the SIGTERM that caused it comes from finding 3.
3. Pass time (new): `runaway.stuck_ticket_ids()` ran one `wq show --json` subprocess per `_runs/wq*` dir since 368227c (09-18). 1052 dirs x 0.17 s = 171.5 s of a 197 s pass. Passes averaged 30 s on 09-17. With the installed 5-min timer drop-in the slock was held about 66% of wall-clock.
4. Budget: cloud gates on local models (wq#2081, PR #676 open) and pass-level `break` on cloud stops strand every candidate sorted behind the first cloud ticket.
5. Supply: 35 of the 36 open local-pinned tickets are structurally undispatchable. The 36th (wq#1692) loops spawn -> release with no progress. Local attempts: HANG 29, CRASH 17, SUCCESS 8.

Architecture answer: separate local from cloud as two partitions inside one pass (local first, pool-scoped stops, split `pass_end` counters), not two daemons. Rationale and design in RCA §4.

## How

The wq#1671 second stuck signal (the wq `attempt` column) is kept; it is now read for every candidate id in one `SELECT ... WHERE id IN (...)` against `WQ_DB` opened read-only, chunked at 500 ids. Any `sqlite3.Error` degrades to the previous per-id `wq_show` path, so the answer can only get slower, never different.

Re-timed against production data: 171.49 s -> 0.03 s, same 33 stuck ids.

## Testing

- `test_stuck_scan_reads_wq_once_not_per_run_dir`: `wq_show` monkeypatched to raise; both stuck shapes (attempt files, wq `attempt` column) detected; under-threshold and done tickets spared.
- `test_wq_rows_falls_back_to_wq_show_when_db_unreadable`.
- `dispatcher/tests/test_runaway.py`: 24 passed. Full `dispatcher/tests` result is recorded in the ticket done-notes.

## Risk / rollback

Read-only query, no schema change, no new dependency. If `WQ_DB` is unreadable the old path runs. Rollback is reverting this commit; the RCA document has no runtime effect.

ISSUE-LOG rows 449 and 450 are staged in RCA §6 because `/home/lumen/kb` is write-denied for workers.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
