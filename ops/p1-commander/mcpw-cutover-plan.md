# mcpw cutover plan — Lumen -> P1 primary/backup (wq#2744)

Everything in this file is Frank-driven, one step at a time, per control-plane
rule 3 ("nothing touches the live control plane unattended... no deploy without
Frank driving + a verified end-to-end test BEFORE declaring success"). Nothing
here has been run. Do not run any of it until BOTH P1 boxes independently pass
their own local health check (`curl localhost:3100/health` on-box, then
`mcpw-diag.py --base-url https://mcp-p1-primary.lumennode.uk` and the -backup
equivalent — note these are the PER-NODE tunnel hostnames, reachable directly,
NOT yet through mcpw's pool) for at least a full day of the watchdog ticking
clean.

## 0. Current state (verified 2026-09-26, re-verify before acting — it changes)

- `wrangler.toml` `MCP_BACKENDS = '[{"name":"lumen","url":"https://mcp-backend.lumennode.uk"}]'`
  — single backend, Lumen only. The router (`src/pool.ts`/`router.ts`) is
  already generic N-backend code (built for the Anubis/Bes 09-24 HA design,
  wq#2610) — this cutover is a **config change**, not new Worker code.
- `src/env.ts` has exactly ONE `CF_ACCESS_SERVICE_TOKEN_ID`/`_SECRET` pair —
  every backend hostname's CF Access app reuses that SAME service token
  (`e23d092b-c2d2-4093-b1ec-ecd1a08ec2c0`, the pattern already used for
  mcp-failover and staged for mcp-anubis). No new Worker secret needed for
  the P1 hostnames.
- The 2026-09-25 mcpw outage left `/authorize` temporarily gated by an
  `AUTHORIZE_PASSPHRASE` form (CF Access app for `mcpw.lumennode.uk` itself
  was deleted mid-incident). **Confirm this is resolved (proper CF Access
  restored) before starting the cutover** — cutting P1 in behind a still-
  broken front door proves nothing.
- wq#2607 (Anubis) / wq#2608 (Bes) DC endpoints exist as artifact sets in
  `foreman/ops/wq2607-dc-endpoint-anubis/` and `.../wq2608-dc-endpoint-bes-a1/`
  but were never actually deployed (fleet-exec grant gap, not a decision).
  wq#2744 supersedes that 3/4-node design with a 2-node P1 pair, Lumen fully
  OUT — do not resurrect the Anubis/Bes deploy steps as part of this cutover
  without a separate, explicit Frank decision to run BOTH plans at once.

## 1. Pre-flight (per node, both P1 boxes, before touching the Worker)

```
curl -s http://127.0.0.1:3100/health                        # on-box, expect 200
python3 mcpw-diag.py --base-url https://mcp-p1-primary.lumennode.uk --save-token /home/p1-primary/.secrets/mcpw-watchdog.token
python3 mcpw-diag.py --base-url https://mcp-p1-backup.lumennode.uk  --save-token /home/p1-backup/.secrets/mcpw-watchdog.token
systemctl status mcp-gateway-watchdog.timer                  # active, on both
journalctl -t mcp-gateway-watchdog --since "1 day ago" | grep -c FAILED   # want 0, or only isolated non-repeating blips
```
These prove each P1 box answers a REAL client-shaped OAuth+initialize round
trip on its OWN hostname, independent of mcpw's pool routing.

## 2. Cutover (single `wrangler.toml` edit + deploy, Frank present)

```
cd /home/lumen/projects/mcp-oauth-worker
cp wrangler.toml wrangler.toml.bak.$(date +%Y%m%d_%H%M%S)_pre_p1_cutover
```
Edit the `[vars]` block:
```
MCP_BACKENDS = '[{"name":"p1-primary","url":"https://mcp-p1-primary.lumennode.uk"},{"name":"p1-backup","url":"https://mcp-p1-backup.lumennode.uk"}]'
```
(Lumen's `mcp-backend` entry is REMOVED here — wq#2744 says Lumen leaves the
control-plane path. If Frank wants Lumen to stay as a 3rd-preference cold
fallback during a transition period instead of a clean cut, say so explicitly
and add it back as a 3rd array element — that's a real design choice, not a
default to assume.)

```
npx wrangler deploy
# note the printed deployment id/version for rollback (§4)
```
The KV `mcp_pool` state reconciles automatically on the next 1-min cron tick
(`loadPool()` in `pool.ts` adds new backend names, drops removed ones) — no
manual KV edit needed. `active` will bootstrap to `p1-primary` (first in
preference order) with `healthy: true` optimistically until the first real
probe corrects it.

## 3. Verify (immediately after deploy, Frank watching)

```
curl -s https://mcpw.lumennode.uk/pool -H "Authorization: Bearer <a real access_token>"
# expect: backends.p1-primary and backends.p1-backup present, lumen ABSENT
python3 mcpw-diag.py --base-url https://mcpw.lumennode.uk --save-token /tmp/verify-token
# expect: PASS through the FRONT DOOR now, not the per-node hostname
```
If `/pool` still shows `lumen` or is missing a P1 entry a full minute after
deploy, the cron probe hasn't ticked yet or the deploy didn't take — do not
proceed to §5 (retiring Lumen's own units) until `/pool` looks right.

## 4. Rollback (any point after §2, before Frank accepts the cutover)

```
cd /home/lumen/projects/mcp-oauth-worker
npx wrangler rollback              # interactive picker, or:
npx wrangler rollback <version-id-from-the-pre-cutover-deploy>
```
This restores the previous `MCP_BACKENDS` (Lumen-only) immediately. Lumen's
own `desktop-commander-mcp`/`cloudflared` units are NOT touched by any step
in this plan and keep running throughout §1-3, so rollback has something
live to fall back to — do not disable/stop Lumen's own DC gateway until
AFTER the cutover has been accepted (see §5).

## 5. Only after the cutover has run clean for a Frank-chosen soak period

Retire Lumen's own control-plane role (this is the actual "Lumen leaves the
control-plane path" step — do it deliberately, separately, not folded into
§2-3):
```
sudo systemctl disable --now desktop-commander-mcp     # on Lumen
# leave the unit files in place (do not delete) for a fast manual revert
# remove the mcp-backend.lumennode.uk ingress line from Lumen's cloudflared
# config.yml once you're confident it's not needed as an emergency 3rd
# backend — or leave it defined-but-unreferenced in MCP_BACKENDS, cheap insurance
```

## 6. Then, separately: the drill (wq#2781)

Do NOT run `p1-ha-drill.sh` as part of the same session as the cutover
itself — prove the cutover is stable on its own first. The drill is its own
ticket, its own Frank-watching session, described in `p1-ha-drill.sh`'s
header and the README.
