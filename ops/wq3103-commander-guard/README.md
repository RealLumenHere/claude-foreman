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
- `dc-call-window.cjs` (wq#3266, RCA `reports/wq3264_mcpw_commander_drops_rca.md`), loaded by the preload:
  - gateway side: clamps `timeout_ms` on `start_process` / `interact_with_process` / `read_process_output` to what is
    left of `MAX_CALL_WINDOW_MS` (default 55000, env-overridable) since the DC spawn, minus a 500 ms reply margin. DC
    cold start (~1.3 s on real DC 0.2.46) comes out of the window, so a clamped call answers within 55 s end to end,
    under the Worker's 70 s abort. Each clamp writes one WARN JSON line to stderr (journal) and one row to the
    `call_window_warn` table in `~/.dc-handles.db` (`DC_HANDLE_DB`). Fail-open on db errors.
  - DC side: supergateway spawns DC with `NODE_OPTIONS=--require=commander-preload.cjs COMMANDER_PRELOAD_ROLE=dc`.
    In that role only the detach hook loads: spawns carrying DC's shell marker (`env.TERM=xterm-256color`, set by
    `terminal-manager.js executeCommand`) get `detached: true` (setsid). supergateway's per-request reap (SIGTERM
    to the group, SIGKILL to the session) spares them, and handle-store reads the orphaned PID back. The role strips
    `NODE_OPTIONS` again, so DC shells inherit nothing.
  - Limit: a detached job's stdout is still a pipe to the DC that started it. Once that DC is reaped, the job's next
    write gets SIGPIPE. Silent jobs (`sleep`, jobs logging to a file, `lumen-run`) survive; chatty ones still die.
    Before this change every job died.
- `apply.sh deploy|flip|unflip|rollback|status`. Deploy = ONE restart, in shadow, dead-man rollback armed before any
  change, disarmed only after loopback-only bind + guard loaded Access certs + real tunnel traffic seen. Flip = file write,
  needs >=1 valid and 0 would-deny tunnel verdicts, then a fresh valid verdict after the flip or it reverts itself.

## Tests (all run on scratch ports / a scratch unit, nothing live)
- `test_commander_guard.mjs` 17/17 (bind, shadow/enforce via mode file without restart, garbage/unreadable file = enforce,
  child does not inherit NODE_OPTIONS, service-token JWT passes, misconfig exits 78). Negative controls confirmed the
  assertions bite: without the NODE_OPTIONS strip 1 fails, with the old guard 4 fail, with anon allowed 3 fail.
- `test_call_window.mjs` 16/16 (wq#3266, DC-shaped fixture, `MAX_CALL_WINDOW_MS=3000`): shell in its own session,
  `sleep 241` alive after the reap while an in-session control and the DC itself are reaped, clamp end to end, all
  three tools clamped, short timeouts and other tools untouched, WARN row + journal line per clamp only, DC shells
  inherit neither `NODE_OPTIONS` nor the role. Runs in `apply.sh deploy` preflight.
- `accept_real_dc.mjs` 6/6 (wq#3266 ticket acceptance, real desktop-commander, default 55000, HOME/db in a temp dir):
  `start_process 'sleep 241' timeout_ms=4000` then `kill -0` 15 s later succeeds; the same probe through a gateway
  without the preload finds the PID dead (the probe can fail); `timeout_ms=120000` returned at 54.5 s (clamped to
  53192). The first cut clamped to a flat 55000 and returned at 56.4 s, which is why the window now counts from the DC
  spawn.
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

## wq#3266 activation (files on disk 2026-10-01, NOT active until Commander restarts; Frank's go)
`/home/lumen/bin/commander-preload.cjs` + `dc-call-window.cjs` are installed (tmp + rename). The running supergateway
keeps the preload it loaded at start, so nothing changes until the next restart. The drop-in already exists, so
`apply.sh deploy` refuses. Restart detached (SAFE SWITCHOVER: Commander and cloudflared ride this unit):

    ssh lumen@100.117.66.102 'sudo systemd-run --unit=dc-wq3266-restart --collect systemctl restart desktop-commander-mcp'
    ssh lumen@100.117.66.102 'curl -s -o /dev/null -w %{http_code} http://127.0.0.1:3100/health; journalctl -u desktop-commander-mcp --since -5min -o cat | grep call-window'

Rollback: restore the previous preload from lumen-bin git (`git -C /home/lumen/bin show 38db38d:commander-preload.cjs`)
by tmp + rename, then the same detached restart. `dc-call-window.cjs` is inert without the preload requiring it.
