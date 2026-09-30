# wq#3225 verify: reset_budget_job Prisma `budget_limits.not` error on the v1.96.2 jump

Verdict: **UPSTREAM FIX CONFIRMED IN SOURCE; LIVE LOG CONFIRMATION STILL PENDING** (prod not yet on 1.96.2)

## What the ticket asked

Confirm that the `Failed to reset budget windows for keys/teams: ... where.budget_limits.not ...`
`prisma.errors.MissingRequiredValueError` (originally diagnosed wq#919, ISSUE-LOG row 403;
`litellm/proxy/common_utils/reset_budget_job.py:637`/`:660` on the pinned 1.83.10 image) is
gone once the fleet's pending v1.96.2 jump (wq#3115/wq#3217) lands, by grepping the litellm
container log for the string after one 10-minute tick.

## What I checked

- Live prod check today (2026-09-30): `curl -s 127.0.0.1:4000/health/readiness` ->
  `"litellm_version":"1.83.10"`. Prod is **still on 1.83.10**, not 1.96.2 — the upgrade window
  op this ticket rides has not landed yet. This matches ISSUE-LOG row 1037 (wq#3115,
  2026-09-29): the litellm-upgrade window op is orchestrator-privileged (`docker`,
  `lumen-window queue`, and the `/home/lumen/.maint-ops` state dir are all outside a build
  worker's reach/allowlist), so it could not confirm a version bump either. `docker logs`/
  `docker ps` are also off this worker's effective allowlist (the per-command allow entries
  are overridden by the blanket `Bash(docker *)` deny in `harness/worker-settings.json`), so
  a live log grep against the current container is not reachable from this lane regardless of
  version — matching the prior attempt's PERMISSION_LOOP outcome when it tried to force that
  path.
- Because the live log check is not yet reachable (prod hasn't been recreated on 1.96.2 yet),
  I verified the fix at the source level instead: fetched
  `litellm/proxy/common_utils/reset_budget_job.py` from the `BerriAI/litellm` GitHub repo at
  tag `v1.96.2` (`raw.githubusercontent.com/BerriAI/litellm/v1.96.2/...`, 882 lines).
  `reset_budget_windows()` (lines 713-785) no longer calls the Prisma ORM
  `find_many(where={"budget_limits": {"not": None}})` that raised the reported
  `MissingRequiredValueError`. It now issues raw SQL via `query_raw`:
  - keys: `'SELECT token, budget_limits FROM "LiteLLM_VerificationToken" WHERE budget_limits IS NOT NULL'` (line 731-733)
  - teams: `'SELECT team_id, budget_limits FROM "LiteLLM_TeamTable" WHERE budget_limits IS NOT NULL'` (line 760-762)

  An inline comment (lines 723-727) documents the reason: "prisma-client-python does not
  support null-filtering on `Json?` columns (no DbNull/JsonNull sentinel — see
  RobertCraigie/prisma-client-py#714)". That is exactly the class of bug wq#919/wq#3225
  reported — the fix sidesteps the Prisma ORM's JSON-null-filter limitation entirely instead
  of patching around it, so the original traceback shape (`where.budget_limits.not`) cannot
  recur from this code path.
- Cross-checked upstream issue history for whether this was already "fixed" earlier and
  regressed: ISSUE-LOG row 403 (wq#919) cites PR#26346 ("[Fix] Reset budget windows failing
  due to Prisma Json? null filter", merged 2026-04-23) as the upstream fix. But
  `github.com/BerriAI/litellm` issue #27300 ("max_budget is ignored after reset") was filed
  2026-05-06 — *after* PR#26346 merged — reporting the identical traceback, and a 2026-06-05
  comment confirms it was "still present on v1.83.14-stable" (3,454 occurrences/24h). So
  PR#26346 did not fully fix the bug for the general case; #27300 was closed 2026-09-12 by
  the stale-issue bot (auto-close on inactivity, not a linked fix commit) — its close event
  is not evidence of an upstream fix. The `query_raw` rewrite found directly in the v1.96.2
  source (above) is the actual fix, independent of what closed #27300.

## Why "gone" can't be marked CONFIRMED yet

The ticket's action is explicitly to verify post-recreate, on 1.96.2. Prod is measured today
at 1.83.10 (evidence above), so there is no live 1.96.2 log to grep. This is a source-level
confirmation that the fix exists and will apply once the pending window op recreates the
container; it is not a live-log confirmation.

## Follow-up (for whoever does the post-recreate verify once wq#3115/wq#3217 actually lands)

Once `curl 127.0.0.1:4000/health/readiness` reports `litellm_version` >= `1.96.2`, grep the
container log for `Failed to reset budget windows` over one 10-minute tick. Expect 0 hits,
per the `query_raw` rewrite confirmed above (`reset_budget_job.py:731-733`, `:760-762`). A
non-zero hit at that point would mean either a different code path regressed or the deployed
image doesn't actually match the `v1.96.2` tag's source — worth re-diffing against the live
container's installed package in that case, not re-assuming this report's source read.
