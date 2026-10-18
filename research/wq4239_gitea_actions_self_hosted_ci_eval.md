# wq#4239 — EVALUATE: Gitea Actions self-hosted CI/CD for foreman PRs

## Verdict

**Feasible and the correct product for this fleet** (unlike wq#4161, which evaluated
GitHub's `actions/runner` against a repo whose actual forge is Gitea — see
`research/wq4161_github_actions_self_hosted_runner_eval.md`). This ticket is the follow-up
wq#4161 itself recommended filing.

- Gitea instance (`100.121.17.5:3000`) reports `version: 1.27.3`
  (`curl http://100.121.17.5:3000/api/v1/version`), well past the 1.19 release that
  introduced Actions.
- Actions is enabled instance-wide: `GET /api/v1/repos/lumen/mailbot/actions/tasks`
  (a public repo, confirmed via `/api/v1/repos/search`) returns `200
  {"workflow_runs":[],"total_count":0}`, not the 404 Gitea returns when
  `DISABLE_ACTIONS` is set. Zero runs recorded — no workflow has ever executed on this
  instance, confirming no runner is currently registered anywhere on the fleet.
- No prior `.gitea/workflows/` directory existed in the foreman repo before this change
  (checked this worktree before adding one).
- Foreman has a real pytest suite to gate on: `tests/`, plus per-module suites under
  `claude-shim/tests`, `prompt-forge/tests`, `token-engine/tests`, `orchestrator/tests`,
  `despag/tests`, `memory/tests`, and loose `test_*.py` files under `limpet/`, discoverable
  from repo root via the existing `conftest.py`.

## Change landed this ticket

Added `.gitea/workflows/test.yml` (`foreman-tests` workflow): on `pull_request` against
`main`, run `pytest -q` on a `self-hosted` runner. This is the minimum viable "PRs trigger
test suite before merge" config — it runs entirely on fleet hardware (the `self-hosted`
runner label, no `github.com` dependency) and will start executing automatically the moment
a runner registers against this repo, with zero further workflow-file changes needed.

## What's NOT done, and why it's physical-gated, not blocked

Registering an `act_runner` (the Gitea Actions runner binary, distinct from GitHub's
`actions/runner`) requires a **runner registration token**, minted via Gitea's admin API
(`POST /api/v1/admin/runners/registration-token`) or the repo/org settings UI, or
`gitea actions generate-runner-token` on the Gitea host itself. The fleet's working Gitea
credential is deliberately scoped to `write:repository,read:user` (ISSUE-LOG #40/#42 — the
same least-privilege token that blocks repo creation also lacks the admin scope a runner
token needs). Minting a wider-scoped token or pulling one from the Gitea admin UI is a
credential action on Frank's own account per instinct 3 (not a step any in-scope token can
take), so it is queued as a physical/credential follow-up rather than a blocker on this
ticket:

1. Frank (or an agent holding an admin-scoped Gitea session) generates a repo- or
   instance-level runner registration token from the Gitea UI (Site Administration →
   Actions → Runners, or repo Settings → Actions → Runners).
2. Install `gitea/act_runner` on Lumen (the only currently-online fleet host — see below)
   and run `act_runner register --instance http://100.121.17.5:3000 --token <token>`, then
   run it as a long-lived service under a dedicated low-privilege service user, not as
   `lumen`/root. (This is not vault/cloudflared/Commander, so instinct 5's
   systemd-run-only rule doesn't gate it, but the same caution applies.)
3. **M80s is not a viable install target yet** — ISSUE-LOG #1244/wq#3703 (cited in
   wq#4161) still blocks M80s physical bring-up. Lumen itself is the only currently-online
   candidate host for the runner.
4. Once a runner registers, the `foreman-tests` workflow added here starts firing on the
   next PR with no further config change.

## Prior art

- `research/wq4161_github_actions_self_hosted_runner_eval.md` — the forge-mismatch finding
  and risk literature (ephemeral single-job runners, workflow-permission scope as the
  dominant real-world risk factor) that motivated checking Gitea Actions specifically
  instead of GitHub Actions.
- ISSUE-LOG #40/#42 — documents why the fleet's live Gitea token can't do admin-scoped
  operations (repo creation, and by the same reasoning, runner-token minting).
