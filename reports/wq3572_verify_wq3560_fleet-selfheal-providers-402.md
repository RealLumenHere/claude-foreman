# wq#3572 — verify wq#3560 (fleet-selfheal providers: treat HTTP 402 as billing-down)

Archetype 8 adversarial verify-pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance, same model as author). PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1651
(commit 3a58efc8 on `pr1651`, landed to main as 5d35d4dd — confirmed identical tree, same commit
message, `git merge-base --is-ancestor 5d35d4dd HEAD` true in worktree at d939f319).

## What the diff does

`ops/wq3354-fleet-selfheal/sh_providers.py`: `classify()` gained a `402 -> "billing"` branch
(previously 402 fell into the catch-all `"error"` bucket, which leaves the episode untouched and
dispatch kept landing on the unfunded deepseek host). `sweep()` gained a parallel `billing` verdict
path mirroring the existing `auth` path: `first_billing` timestamp, `disabled_billing` flag, cooldown
entries tagged `source=fleet-selfheal`, and a `top up <host>` ticket (physical, funding tracked in
wq#3557) instead of `rotate key`. The all-hosts-down gateway-key short-circuit
(`every_host_auth`) correctly excludes `billing` from its `all(v in (auth, gateway-auth))` check, so
a billing-down host never gets mistaken for a gateway master-key problem, and the `ok` recovery path
pops/resolves `disabled_billing`/`first_billing` symmetrically with the auth pair.
`tests/test_resource_classes.py` gained `PROVIDER_402`, a classify-level assertion, a 10-minute
disable+recover test, and an every-host-402 test (not-a-gateway-key-page).

## What I checked

- **Tree identity**: byte-diffed `pr1651` vs my worktree HEAD for `sh_providers.py` — identical.
  The PR's code is already live on `main`.
- **Unit tests**: `python3 -m pytest ops/wq3354-fleet-selfheal/tests/test_resource_classes.py -q`
  → 15 passed.
- **Probe can fail (wq#2630)**: reverted the one-line `402 -> "billing"` branch to `402 -> "error"`
  in the worktree, reran the suite → 3 of 15 tests failed exactly as expected
  (`test_classify_tells_provider_auth_from_gateway_auth`,
  `test_provider_402_for_ten_minutes_cools_its_rungs_then_recovery_reenables`,
  `test_every_host_402_files_per_host_tickets_not_a_gateway_key_page`). Reverted the edit;
  `git status --short` clean, suite back to 15/15. The acceptance probe is real, not vacuous.
- **Liveness** (ops_verified(wq3354-fleet-selfheal) per ticket): `install.sh --verify` itself is
  not on this worker's Bash allowlist (no `ops/*install.sh` or `bash ops/**` entry in
  `harness/worker-settings.json`) and the deploy target `/home/lumen/agents/fleet-selfheal/` is
  outside this worker's `--add-dir` set, so I could not run the contract script or byte-diff the
  deployed file directly. Checked the parts that were reachable:
  `systemctl is-active`/`is-enabled lumen-fleet-selfheal.timer` → active/enabled;
  same for `lumen-leakwatch.service` → active/enabled. Combined with the tree-identity check
  above (the exact PR tree is on `main`), this is strong but not complete liveness evidence —
  flagging the allowlist/add-dir gap rather than retrying blocked shapes.
- **Cross-reference integrity**: `wq show 3557` — real ticket, `frank_gated`, title/detail match
  the PR's claim ("top up the DeepSeek API balance (HTTP 402 Insufficient Balance since
  2026-10-01)"). Not a dangling reference.
- **Adversarial angle — could LiteLLM itself synthesize a 402** (so the fix misattributes a
  gateway/budget problem to the provider as a false "top up" ticket)? Pulled
  `litellm/exceptions.py` (`BudgetExceededError.status_code = litellm.budget_exceeded_status_code`)
  and `litellm/__init__.py` from upstream: default `budget_exceeded_status_code = 422`, the only
  documented alternate is `429`. LiteLLM's own budget/rate-limit enforcement never emits 402, so a
  402 reaching this probe is a passthrough of the upstream provider's status — the fix's premise
  holds.
- **Mixed-verdict logic read**: traced `every_host_auth` and the `"gateway-auth" in results.values()`
  short-circuit for a one-host-auth/one-host-billing sweep — `billing` is excluded from the
  all-auth set, so both per-host tickets (rotate-key, top-up) still file independently rather than
  collapsing into the wrong gateway-key page. No test pins this exact mix, but the logic is a
  direct, non-surprising extension of the pre-existing auth-only `all(...)` check.

## Verdict

**CONFIRM.** The fix is correct, tested with a falsifiable probe, already live on `main`, and its
physical-task follow-up (wq#3557) is a real, correctly-gated ticket. No contradiction found; no
escalation to opus-5@high.

## Gaps for the record (not blocking)

- `bash ops/wq3354-fleet-selfheal/install.sh --verify` is not reachable from this worker's Bash
  allowlist, and `/home/lumen/agents/fleet-selfheal/` is outside its `--add-dir` set. Widening
  either would let a future verify-pass byte-diff the deployed file directly instead of relying on
  tree-identity + systemd status as a proxy.
