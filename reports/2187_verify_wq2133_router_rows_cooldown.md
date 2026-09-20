# wq#2187 — verify of wq#2133 (34e622c, PR #738, routing rows + status/cooldown-aware router)

Verdict: **CONFIRMED.** No contradiction, no escalation. Verifier: claude-sonnet-5@high, cold read, 2026-09-20.

## Checks run

| Claim (wq#2133 ticket / PR #738) | Evidence | Result |
|---|---|---|
| `route mechanical-edit` returns `local-worker` | `python3 orchestrator/router.py route mechanical-edit` → `{"model": "local-worker", "pool": "local"}` | confirmed |
| With every local backend cooling, select falls to the cloud rung | cooldown file with hathor/nephthys/qwen36 in the future → `mechanical-edit` = `sonnet-5@low`, `probe-build` = `sonnet-5@medium`; also holds for `{"until": ..}`, ISO strings, and `name@local` keys | confirmed |
| Group stays up while one member is live | hathor only cooled → `local-worker@local`; nephthys only cooled → `local-worker@local`; hathor+nephthys cooled → cloud (qwen36-local is `status: down`, counted out) | confirmed |
| Expired / unreadable / non-dict cooldown = no cooldown | past epoch, `not json`, `[1,2]` → `local-worker@local` | confirmed |
| Fails-walk position survives a cooled rung | all cooled, fails=3 → `probe-build` lands on `haiku-4-5`; fails=1 → `sonnet-5@medium` | confirmed |
| No live rung silently dropped by the status gate | every rung of every routing chain checked against `availability_check`: 0 unavailable; `select` returns non-None for all archetypes at fails 0..3 (`.tmp/probe3.py`) | confirmed |
| Rollback lever `FOREMAN_LOCAL_CAPACITY=static` | `router.py:494`; `test_static_lever_turns_the_gate_off` | confirmed |
| `local_standard_engineering: false` default | `Manifest.local_standard_engineering` is False on the shipped models.yaml; standard-engineering stays `sonnet-5@high` | confirmed |
| `verify_pass.local_author_always` stays true | `test_verify_pass_local_author_always_stays_true` | confirmed |
| agents.yaml primary tuples match models.yaml | `agents_spec.lint()` → `[]` | confirmed |
| simpill disabled + lint FAILs an enabled lane with no LANE_REPO row | `validate_lane_repos` probe: simpill enabled → FAIL; disabled → clean; unknown lane enabled → FAIL; human lane exempt; string `"true"` not counted (dispatcher treats non-bool as disabled, `foreman_dispatcher.py:265`). `dispatch_config_lint.py check --dry-run` on committed config: no FAIL | confirmed |
| served_ctx truth for the local models | live `/api/ps`: hathor (llama3.1:8b) `context_length: 32768`, nephthys (qwen3:14b) `context_length: 32768`; matches models.yaml and config.json. Isis unreachable (wq#2140), 8192 left as last measured, noted as such in models.yaml | confirmed (Isis unverifiable) |
| No import-path collision from `sys.path.append(dispatcher)` in router.py | no duplicate `.py` names across dispatcher/, orchestrator/, harness/; append (not insert) | confirmed |

## Suites (worktree at 34e622c)

- orchestrator: 126 passed
- dispatcher: 474 passed (matches the PR body)
- harness: 1332 passed, 1 failed — `test_liveness_ops_verified.py::test_drifted_target_still_blocked`.
  Unrelated to the diff (`harness/liveness.py` untouched): the assertion needs `"differs"` in a detail string that the worktree's long `.tmp/pytest-of-lumen/...` tmp path truncates.
  The PR's "8 pre-existing stop-gate fails" note did not reproduce here (1 fail, different test); the delta is environment, not this change.

## Notes for follow-up (not defects)

- The `local-worker` member liveness test in `availability_check` uses `models.yaml` status of the member name. Today only `qwen36-local` has a non-LIVE row, so its permanent "out" status is what makes the two-cooled case fall to cloud. Correct for now; if Isis returns, flip its status and it rejoins.
- `select()` resolves availability after the fails-walk on purpose, so a cooled rung still consumes a position. Tested, and reads as intended.
