# wq#2189 — verify of wq#2184 (d024083, PR #737, second wall honours the lint's advisory bucket)

Verdict: **CONFIRMED.** No contradiction; no escalation. One pre-existing CLI bug found (not in wq#2184's diff), logged.
Verifier: claude-sonnet-5@high, cold read, 2026-09-20. Everything below was executed against the live queue and disk.

## Checks

| Claim | Evidence | Result |
|---|---|---|
| Both walls read one classification | `capability_lint._derive_dirs` records `advisory_paths` (capability_lint.py:554-565); `derive` (:783-802) and `advisory_paths_for` (:805-823) run the same pass; dispatcher passes `derived.advisory_paths` (foreman_dispatcher.py:743), fworker passes `advisory_paths_for(...)` (fworker.py:1018) | confirmed |
| Path-string keys line up | `capability.cited_paths` (:123-129) and `capability_lint.cited_paths` (:544-551) use the same regex and the same `rstrip(".,;:")`, so `p in advisory_paths` cannot miss on punctuation | confirmed |
| Deny-list stays hard | Denied paths are appended to `refused` (capability_lint.py:586, :608), never `advisory_paths`; live corpus: 0 dispatchable tickets cite a `dir_denied` path that is not a worktree mention (200 open/blocked tickets scanned) | confirmed |
| Secret set stays refused | `capability_replay --json` over wq#1466 1467 1491 1619 1975 2114 1690 2070 2089 -> `capability_denied_by_policy`; wq#1981 -> `needs_unresolved`; wq#2120 -> `needs_unresolved` (sweep >48 h stale, fail closed) | confirmed |
| Explicit `needs:dir` on unreachable path still refused | `path_problems` checks `needs_dirs` before the advisory skip (capability.py:145-149); `test_explicit_needs_dir_on_an_unreachable_path_is_refused`, `test_explicit_dir_is_refused_even_when_the_same_path_is_in_the_advisory_set` | confirmed |
| node_unknown / policy refusals untouched | preflight order unchanged after path check (capability.py:307-318); `test_node_unknown_stays_a_hard_refusal_beside_an_advisory_path` | confirmed |
| Lever off restores the hold at both walls | `preflight` zeroes `advisory_paths` when `advisory_enabled()` is false (capability.py:297-300); `advisory_paths_for` returns `[]` (:813). Corpus replay with `FOREMAN_CAPABILITY_ADVISORY=off`: 0 rows dispatchable-with-advisory, the 39 that were move back to `capability_denied_by_policy` / `needs_unresolved` via the legacy buckets | confirmed |
| fworker spawn-time check agrees with the dispatcher | Re-ran `derive` + `preflight(worktree=..., advisory_paths=advisory_paths_for(...))` for all 200 open/blocked tickets: 151 dispatchable at the dispatcher, **0** refused at the fworker wall | confirmed |
| Every `cited_path_unreachable` row is gone | Live replay of 200 open/blocked tickets: 0 `cited_path_unreachable`; 151 dispatchable (39 with a `needs_advisory` note), 10 `capability_denied_by_policy`, 39 `needs_unresolved` | confirmed |
| Tests | Touched files (`test_capability_second_wall.py`, `test_capability.py`, `test_capability_replay.py`): 92 passed. `dispatcher/tests`: 474 passed. `harness/tests`: 1382 passed, 5 failed | see below |

## The 5 harness failures are not from wq#2184

- `test_memory_injection.py` x4: `ModuleNotFoundError: No module named 'recall'` at `fworker.py:833`, a memory-recall import that is not on the worker's sys.path. The only commit since d024083 that touches these files is cd35dab (memory), not the capability change.
- `test_liveness_ops_verified.py::test_drifted_target_still_blocked`: asserts the detail contains "differs"; the worker's pytest tmp dir sits under the worktree, so the detail says "absent". Ambient-path dependence, no capability code involved.

## Findings

1. **Pre-existing bug, not wq#2184's:** `dispatcher/capability_replay.py:114-116` loops `for a in r["advisory"]`, shadowing the argparse namespace `a`, so text mode crashes with `AttributeError: 'str' object has no attribute 'json'` on the second ticket after any ticket with an advisory finding (blame: f5a4e85, wq#2131). `--json` is unaffected; the wq#2184 replay proof was therefore only reproducible in `--json` mode. Logged as ISSUE-LOG #498; fix is a one-word rename.
2. **Number drift, not a defect:** the ticket predicted 28 rows flipping, the closure note says 42. The pull-734 ticket list is not reachable from this worker (`gh` is not installed), so the exact 44-ticket set could not be replayed; the full live open/blocked corpus was replayed instead (above) and shows none left.
3. **Residual, fail-safe direction:** `FOREMAN_NEEDS_LINT_MODE` and `FOREMAN_CAPABILITY_ADVISORY` are read from each process's own environment. If a systemd drop-in sets either on the dispatcher unit only, fworker computes an empty `advisory_paths` and holds a ticket the dispatcher approved (SpawnRefused after claim). It can only over-refuse, never over-admit. Systemd drop-ins are outside this worker's readable dirs, so not checked.
