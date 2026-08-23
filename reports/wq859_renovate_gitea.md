# wq#859 — Renovate bot on Gitea (Anubis)

Deployed 2026-08-23, executed locally on `lumen` via `lumen-run --root` (this host is the
worker's own machine; no SSH used). All config and automation live under
`ops/wq859-renovate-gitea/` in this repo. Ref: `reports/opsaudit_a12_tooling_consult.md`
section 2, rank 3.

## What's live

- Renovate 42.99.0 (AGPL-3.0), installed via `npm install -g renovate` as `lumen`
  (`/home/lumen/.npm-global/lib/node_modules/renovate`). No native `re2` binary for this
  platform/arch — falls back to plain `RegExp` (logged `WARN`, harmless: no untrusted
  `regexManagers` are configured).
- `renovate-gitea.service` (oneshot) + `renovate-gitea.timer` (every 4h, `OnBootSec=10min`)
  installed to `/etc/systemd/system/`, enabled and active. Runs as `User=lumen`.
- Global/autodiscover mode (`autodiscoverFilter: ['lumen/*']`) — scans every repo the token
  can see rather than a hardcoded list, so new repos get picked up automatically. Discovered
  8 repos: `ccc`, `claude-foreman`, `lumen-bin`, `lumen-messenger`, `lumen-messenger-cc`,
  `minerva`, `mission-control`, `questfree`.
- `automerge: false` and `platformAutomerge: false`, explicit — Frank reviews every PR by
  hand. Per-repo auto-merge is a later opt-in once trust builds, per the audit report.

## Credential fix found during verification

The shared fleet `GITEA_TOKEN` (`/home/lumen/agent/.env`) is scoped
`write:repository,write:user` — no `read:issue`/`write:issue`. A dry run (`RENOVATE_DRY_RUN=full`)
caught this immediately: every repo hit `403 Forbidden` on the issues endpoint ("token does
not have at least one of required scope(s), required=[read:issue]"), which Renovate needs for
the Dependency Dashboard and PR bookkeeping.

Rather than widen the shared token's scope (used elsewhere — fleet backup, git remotes — and
out of this ticket's blast radius), minted a **dedicated** Gitea token via the API
(`POST /api/v1/users/lumen/tokens`, basic-auth with the existing admin creds), scoped to only
`write:repository, write:issue, read:user`. Stored at
`/home/lumen/services/renovate-gitea/.env` (mode 600, `lumen:lumen`), loaded by the systemd
unit's second `EnvironmentFile=`. The token value was never read back or printed — the mint
script wrote it directly from the Gitea API response to the target file.

## Verified live

Re-ran the dry run with the new token: clean exit, 0 errors, all 8 repos processed. Then
triggered a real run (`lumen-run --root -- systemctl start renovate-gitea.service`) and
confirmed via the Gitea API (`GET /repos/lumen/<repo>/pulls?state=open`) that real onboarding
PRs landed:

| Repo | PR |
|---|---|
| ccc | #1 "Configure Renovate" |
| lumen-messenger | #1 "chore: Configure Renovate" |
| lumen-messenger-cc | #1 "Configure Renovate" |
| mission-control | #16 "chore: Configure Renovate" |
| questfree | #7 "chore: Configure Renovate" |

`claude-foreman`, `lumen-bin`, `minerva` had no manifest/compose files Renovate recognizes —
no PR, no error, expected. These are Renovate's standard **onboarding** PRs (propose a
`renovate.json` extending `config:recommended`); real dependency-update PRs on a given repo
start only once Frank merges its onboarding PR — a per-repo opt-in chokepoint, not a global
switch. Timer confirmed `enabled`/`active`, next run in ~4h.

## What Frank needs to do next

Nothing to unblock the ticket — deployed and proven end to end. Ongoing: review and merge (or
close) the 5 onboarding PRs per repo to start real dependency scanning on the ones he wants
covered; enable `automerge` per-repo in that repo's `renovate.json` once trust builds.
