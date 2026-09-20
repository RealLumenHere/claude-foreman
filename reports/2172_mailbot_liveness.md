# wq#2172 — is the deployed lumen-mailbot worker on the wq#2168 commit?

Status: **UNCONFIRMED from a worker; tooling landed so a token shell can confirm in one command.**

## What is known

- The wq#2168 fix is merged on mailbot `main`: `30b9bb9` (merge of `248c163`), touching
  `src/leakfilter.js` and `ops/wq2147-verify/probe.mjs`, 2026-09-20T16:36:08Z.
- Whether Cloudflare runs it is unknown. `deploy.sh` and `wrangler deployments list` both need
  `CLOUDFLARE_API_TOKEN`; a build worker has none (instinct 4). `src/worker.js` exports only
  `email()` and `scheduled()` (`export default` at line 752), so there is no unauthenticated
  endpoint to read a version from, and `deploy.sh` passes no `--message`, so a deployment
  carries no commit stamp.

## What landed

`ops/wq2172-mailbot-liveness/check_live.py` (tests: `ops/tests/test_wq2172_mailbot_liveness.py`,
12 pass). From a shell with the token:

    CLOUDFLARE_API_TOKEN=... python3 ops/wq2172-mailbot-liveness/check_live.py

It runs `wrangler deployments list --name lumen-mailbot --json`, finds the newest deployment,
and compares it to the last mailbot commit touching `src/`, `wrangler.toml`, or `package*.json`.
Exit 0 CURRENT, 1 STALE (then run `/home/lumen/mailbot/deploy.sh`; its eval gate runs first),
3 UNCONFIRMED (no token / wrangler error / no deployments). Without a token it exits 3 without
calling wrangler; verified: `UNCONFIRMED: CLOUDFLARE_API_TOKEN is not set`.

Limit: un-stamped deployments are judged by time, so a deploy cut from an older tree after the
commit reads CURRENT. The stamp follow-up below removes that.

## Not done, and why

- `./deploy.sh` and `wrangler deployments list` were not run: no token.
- The build stamp was not added to the worker: `/home/lumen/mailbot` is a primary tree and a
  worker may not edit it. Follow-up: `deploy.sh` should call
  `npx wrangler deploy --message "$(git rev-parse --short HEAD)"`; `check_live.py` already
  reads `workers/message` and `workers/tag` and treats a matching sha as an exact hit.
- `node mailbot/ops/wq2147-verify/probe.mjs` was refused ("requires approval", same as
  ISSUE-LOG #475), so the needsFrank() fix itself was not re-run here.

## ISSUE-LOG row (kb is a primary tree; workers cannot edit it, guard.sh wq#1719)

| 476 | wq#2172: cannot confirm deployed lumen-mailbot is on 30b9bb9; also refused for a worker: `mailbot/node_modules/.bin/wrangler`, `node mailbot/ops/wq2147-verify/probe.mjs` (requires approval), `test -n "$VAR"` (simple_expansion) | wrangler needs CLOUDFLARE_API_TOKEN a worker never holds (instinct 4); worker.js has no version endpoint; deploy.sh passes no `--message`; wrangler/node not on the per-command allowlist | ops/wq2172-mailbot-liveness/check_live.py + 12 tests; follow-up to stamp deploys with the sha | OPEN |
