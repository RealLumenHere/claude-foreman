# wq#3103: Commander :3100 loopback bind + Access JWT guard

Problem: `desktop-commander-mcp` (supergateway) listened on `*:3100` and answered an anonymous MCP `initialize`
with 200, so any local process (headless chrome via chrome-mcp, a container, a compromised script) could get a
Lumen shell. Off-box exposure was already closed (wq#2808/#2986 raw drops, wq#3089 v6 default-drop).

Why not a bearer token (Frank's first ask): mcpw's `router.ts` deletes the client Authorization header and calls
`mcp-backend.lumennode.uk` with a CF Access service token, so a static bearer needs code+secret redeploys of two prod
OAuth workers, and a token in the unit env is readable by the same uid via `/proc/<pid>/environ`, i.e. by the attacker
this is meant to stop. A signed Access JWT can't be forged locally without Cloudflare's key. Same pattern as
chrome-mcp :3103 / memory-mcp :3104 (wq#2751 + wq#2768), which already runs in enforce.

## What ships
- `commander-preload.cjs`: loads `loopback-listen.cjs` (bind 127.0.0.1) then `access-jwt-guard.cjs`, then **deletes
  `NODE_OPTIONS`** so desktop-commander and every node process a Commander shell starts do not inherit the guard
  (each would otherwise fetch Access certs and write guard lines to stderr, changing `npx`/`wrangler` output).
- `../wq2768-access-guard/access-jwt-guard.cjs`: new optional `ACCESS_GUARD_MODE_FILE`. Re-read at most once a second;
  `shadow`|`enforce`; missing file = `ACCESS_GUARD_MODE`; unreadable or any other content = enforce. Lets the mode flip
  **without restarting Commander** (a restart drops every session). Backward compatible: chrome-mcp/memory-mcp
  don't set it.
- `apply.sh deploy|flip|unflip|rollback|status`. Deploy = ONE restart, in shadow, dead-man rollback armed before any
  change, disarmed only after loopback-only bind + guard loaded Access certs + real tunnel traffic seen. Flip = file write,
  needs >=1 valid and 0 would-deny tunnel verdicts, then a fresh valid verdict after the flip or it reverts itself.

## Tests (all run on scratch ports / a scratch unit, nothing live)
- `test_commander_guard.mjs` 17/17 (bind, shadow/enforce via mode file without restart, garbage/unreadable file = enforce,
  child does not inherit NODE_OPTIONS, service-token JWT passes, misconfig exits 78). Negative controls confirmed the
  assertions bite: without the NODE_OPTIONS strip 1 fails, with the old guard 4 fail, with anon allowed 3 fail.
- `../wq2768-access-guard/test_access_guard.mjs` 30/30 still passes on the patched guard.
- `test_apply.mjs`: apply.sh against scratch unit `dc-scratch-3103` (deploy, flip refusal, would-deny blocks flip, flip
  auto-revert, flip success, rollback, deploy auto-rollback without tunnel traffic, dead-man firing after apply.sh is
  SIGKILLed).

## Callers of :3100 that break or change
- Health gates that do an anonymous `POST /mcp initialize` and require 2xx would now see 403 and auto-roll-back a healthy
  unit. Fixed in lumen-bin `dc-redeploy.sh` (gate = active + listening + `GET /health` 200). `GET /health` stays open.
- Not changed (one-shot drills, not gates): `wq829-mcp-gateway/apply.sh` smoke + `loadtest.sh`, `wq2605.../stress_test.sh`,
  `bin/mcpw-stateful-cutover.sh` (informational; stateful is a dead end). Run loadtests with the guard in shadow (`apply.sh unflip`).
- Other nodes with the same anonymous exposure (bes-mcp-failover wq#3094, Anubis/Station) need their own AUD; the
  Anubis `mcp-gateway-watchdog.sh` probes `localhost:3100/mcp` anonymously and needs the same /health-based gate first.
- Everything else hitting :3100 is cloudflared (`localhost:3100`, loopback, unaffected), Gatus (`network_mode: host`,
  `/health`), mcp-pressure-guard (`/health`).

## Apply (Frank drives, Commander restarts once)
    ssh lumen@100.117.66.102 'sudo systemd-run --unit=dc-guard-apply --collect bash <repo>/ops/wq3103-commander-guard/apply.sh deploy'
    ssh lumen@100.117.66.102 'sudo bash <repo>/ops/wq3103-commander-guard/apply.sh status'
    ssh lumen@100.117.66.102 'sudo systemd-run --unit=dc-guard-flip --collect bash <repo>/ops/wq3103-commander-guard/apply.sh flip'
Verify enforce: `curl -X POST http://127.0.0.1:3100/mcp` (anonymous initialize) must be 403; Commander from claude.ai still works.
Rollback: `apply.sh rollback` (restarts once) or `apply.sh unflip` (no restart).
