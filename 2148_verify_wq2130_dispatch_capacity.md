# wq#2148 — verify-pass of wq#2130 (PR #706, ce58824)

Verdict: **CONFIRM**. Every PROOF item on wq#2130 holds; two low-severity design gaps and one
incident of mine are recorded below. No contradiction, so no opus-5 escalation.

## Evidence

- PROOF tests: `pytest` on the four new files, 54 passed (dispatcher/tests/test_circuit_breaker_pools.py,
  test_local_backends_lint.py, test_local_capacity_dispatch.py, harness/tests/test_concurrency_pools.py).
- The PR's "11 dispatcher failures" do not reproduce. `pytest dispatcher/tests` at 091d610 (includes the PR):
  443 passed, 1 failed. The 1 is `test_capability_replay[1407]`; it fails identically at the PR's parent
  fdd0db3, so it is pre-existing, not this diff. The rest were the wq#2139 `wq add` refusal (ISSUE-LOG #471).
- Harness/orchestrator `-k "concurrency or spawner or router or dispatch_cmd"`: 134 passed. I did not
  re-run the full harness suite (the PR's 50-89 unclassified failures); nothing in the diff is reachable
  from the ones it classified.
- Live config: `dispatch_config_lint.py check` clean (2 unrelated WARNs). Live capacity snapshot:
  hathor healthy 1/2 free, nephthys 2/2, qwen36-local down -> 0, `local-worker` group 2 free; probe
  0.02 s. `FOREMAN_*` env for `hathor-llama8b@low` resolves to first_frame 1500 / ctx 32768.
- Real attempt records carry `model` (1785 of 1833 under `_runs`), so the per-pool split has data.
  `circuit_breaker.py status` on live runs: cloud 6/41, local 0/5, neither tripped.
- `ops/wq2148-verify/probe_local_breaker.py` (temp dirs, notify stubbed): 12 local OOMs across mixed
  spellings (`@low`, group name, deepseek) + 10 cloud successes -> cloud not tripped, no DISARM,
  local pause written once, no re-notify while paused. Model-less KILLED records trip cloud, not local.
- Pool constants at runtime: LOCAL_POOL_MODELS = qwen36-local, nephthys-qwen14b, hathor-llama8b, local-worker;
  CHEAP_CLOUD = deepseek-v4-flash (`is_local_model` True, `is_mac_local_model` False).

## Findings (none blocks)

1. **Pause shorter than the breaker window** — `WINDOW_MINUTES` is 90, pause is 60. Failures younger than
   30 min at trip time are still in the window at expiry, so the breaker re-trips on the first pass after
   expiry and sends a second urgent ntfy (probe line 4). Worst case ~120 min paused, no half-open probe.
   Fix later: window the local pool by the pause's `tripped_at`, or set pause >= window.
2. **Group-routed units are invisible to a member** — `free_slots("hathor-llama8b")` subtracts only units
   pinned to hathor (`local_capacity.py:190`). A `local-worker` unit that LiteLLM lands on hathor uses a
   slot the direct count does not see, so hathor can run slots+1. The group side is counted correctly.
3. **`FOREMAN_FIRST_FRAME_S` / `FOREMAN_SERVED_CTX` have no consumer yet** — set by
   `router.dispatch_cmd` (`orchestrator/router.py:1090`), read by nothing in fworker. Matches the ticket's
   "pass to the spawn"; the consumer must land in a later step or the values are inert.

## Incident (mine)

My first, unstubbed probe run sent two real urgent ntfy pushes titled "foreman local breaker" ("local pool:
12/12 attempts failed ... paused 60m") on synthetic data. Nothing on disk was affected (temp dirs) and no
pause was written to the live runtime dir. Treat those two pushes as noise. The committed probe stubs
`notify_sinks`.
