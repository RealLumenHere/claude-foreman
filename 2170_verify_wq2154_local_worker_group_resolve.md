# wq#2170 — verify of wq#2154 (36d1333, PR #721, resolve local-worker group at admit time)

Verdict: **CONFIRMED.** No contradiction, no escalation. Verifier: claude-sonnet-5, cold read, 2026-09-20.

## Checks run

| Claim (PR #721 / wq#2154 ticket) | Evidence | Result |
|---|---|---|
| `pick_member(group)`: healthy, not-cooling member with most free slots, ties in config order | `dispatcher/local_capacity.py:215-232`; real `config.json` + fake probe: all up → `hathor-llama8b`; hathor full (2/2) → `nephthys-qwen14b`; all down → `None`; `local-worker@low` → `hathor-llama8b`; non-group → `None` | confirmed |
| `_admit_local` resolves the group, logs `local_slots_full` when no member is free/healthy/cooling | `dispatcher/foreman_dispatcher.py:1466-1477`; test `test_group_ticket_logs_local_slots_full_when_every_member_is_down_or_cooling` | confirmed |
| Spawn carries member as `--model`, env comes from the member, `member=` logged | `foreman_dispatcher.py:1605-1631`, `spawner.py:219-243` (governor check runs on the override), `router.py:1044-1068`; tests at `test_local_capacity_dispatch.py:218,312` | confirmed |
| Ticket column stays `local-worker` | override applies only to the built command, nothing writes the column | confirmed |
| Slot accounting reads the unit's own `--model` | `concurrency.unit_model` run live: `fworker-wq2151` → `claude-opus-5`, `fworker-wq2170` → `claude-sonnet-5`, no such unit → `None` (fallback to column); glob does not match `wq210-*` for `wq21` | confirmed live |
| Defect being fixed is real | `foreman_usaged.py can-dispatch local-worker` → `reasons: []` (Anthropic path); `can-dispatch hathor-llama8b` → `local rung: governor-exempt` | confirmed live |
| `LOCAL_POOL_MODELS` restored, `MAC_POOL_MODELS` added | `harness/concurrency.py`: LOCAL = MAC + CHEAP_CLOUD, `is_mac_local_model` uses MAC; deepseek is not a Mac slot | confirmed |
| `git diff e33e083 -- dispatcher/observed_burn_core.py` empty | 0 bytes | confirmed |
| `config.json` live values | nephthys 2400, hathor 1500, qwen36-local ctx 32768 / ff 900; group row carries only `group` | confirmed |
| Fworker's verify-pass filing sees the member, not the group | `fworker.py:2598` passes `--author-model args.model` (the member), so `judgment.verify_required` matches its prefix list | confirmed |
| Touched suites green | `test_local_capacity_dispatch.py test_concurrency_pools.py test_spawner.py` → 57 passed | confirmed |
| No regressions | full `dispatcher/tests harness/tests`: head 2 failed / 1698 passed; parent 56bfbfe 2 failed / 1686 passed. Same two failures on both: `test_capability_replay[1407]`, `test_liveness_ops_verified::test_drifted_target_still_blocked`. +12 tests, 0 new failures | confirmed pre-existing |

## Residual gaps (not contradictions; out of wq#2154's dispatcher scope)

1. Manual spawn paths (`router.py` CLI `dispatch_cmd`, `spawner.dispatch_batch`) do not resolve the group, so a `local-worker` ticket started by hand still reaches the governor as `local-worker`. The dispatcher path is fixed.
2. `orchestrator/judgment.file_verify_pass` falls back to the ticket column when `--author-model` is absent, which is `local-worker` for a group ticket and misses `local_author_always`. The fworker path always passes the member.
3. Within one pass, a just-spawned unit whose `systemctl show` ExecStart is not yet readable is cached at its column (`local-worker`). It still consumes group capacity but not a member slot until the next pass, so a second group ticket could pick the same member. Not reproduced; the unit is normally loaded by the time `lumen-run start` returns.
4. Static mode (`FOREMAN_LOCAL_CAPACITY=static`) routes to the first configured member without a health check. Documented in the `first_member` docstring.
