# wq#859 — Renovate bot on Gitea

Self-hosted [Renovate](https://github.com/renovatebot/renovate) (AGPL-3.0) scanning every
`lumen/*` repo on Gitea (host Anubis, `http://100.121.17.5:3000`) for outdated container image
tags in compose files and outdated package versions in manifests (package.json,
pyproject.toml, etc.), opening PRs with changelogs. Runs in global/autodiscover mode from
Lumen on a systemd timer — not per-repo CI, since these repos have none.

Ref: `reports/opsaudit_a12_tooling_consult.md` section 2, rank 3.

## Prerequisite (one-time, as `lumen`, no root)

```bash
npm install -g renovate
```

Installs to `/home/lumen/.npm-global/lib/node_modules/renovate`. Update later the same way
(`npm install -g renovate` again); no service restart needed since the timer starts a fresh
process each run.

## Deploy (root — `lumen-run --root` on Lumen itself)

```bash
/home/lumen/bin/lumen-run start --root -- bash /home/lumen/worktrees/wq859-a1/ops/wq859-renovate-gitea/install.sh
```

Copies `config.js` to `/home/lumen/services/renovate-gitea/` (owned by `lumen`, since the
service runs `User=lumen`) and the unit files to `/etc/systemd/system/`, then enables the timer.

## Config

`config.js` sets `platform: gitea`, reads `endpoint`/`token` from `GITEA_URL`/`GITEA_TOKEN` in
`/home/lumen/agent/.env` (via the unit's `EnvironmentFile=`, never hardcoded), autodiscovers
every `lumen/*` repo, and extends `config:recommended` for onboarding. **`automerge: false`
and `platformAutomerge: false`** — Frank reviews every PR by hand; auto-merge is a later,
per-repo opt-in once trust builds (per the audit report).

First run per repo opens a Renovate "onboarding" PR proposing a `renovate.json` — dependency
PRs start only after that's merged. This is standard Renovate behavior and gives Frank a
chokepoint to skip repos he doesn't want scanned.

**wq#1504 — Alpine `apk` package tracking:** `customManagers` enables the `apk` datasource for
Dockerfiles (any `Dockerfile`/`Dockerfile.*`). Base-image tags (`nginx:alpine`,
`postgres:15-alpine`) are already tracked by the default `docker` datasource — no config needed.
For a specific package pinned via `apk add pkg=version` inside a Dockerfile, opt that line in
with an annotation comment above an `ENV ..._VERSION=` line, e.g.:

```dockerfile
# renovate: datasource=apk depName=curl versioning=loose
ENV CURL_VERSION="8.9.1-r1"
RUN apk add --no-cache curl=$CURL_VERSION
```

## Verify

```bash
systemctl status renovate-gitea.timer
systemctl start renovate-gitea.service   # force an immediate run
journalctl -u renovate-gitea.service -n 100 --no-pager
```

Expect an onboarding PR (or a "Dependency Dashboard" issue) to appear on each `lumen/*` repo
that has compose/manifest files Renovate recognizes.

## Uninstall

```bash
systemctl disable --now renovate-gitea.timer
rm /etc/systemd/system/renovate-gitea.{service,timer}
systemctl daemon-reload
```
