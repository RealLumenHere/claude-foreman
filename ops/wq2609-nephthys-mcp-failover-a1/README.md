# wq#2609 — Nephthys: re-enable mcp-failover + cloudflared, add T2 watchdog

## Status: artifacts staged, NOT applied to Nephthys

Everything in this directory that is pure content (deploy script, verify
script, this writeup, the un-retire-checklist doc at
`../../docs/fleet/UN_RETIRE_CHECKLIST.md`) is written. Nothing has been
installed, enabled, or requested against Nephthys — same shape of blocker as
`ops/wq2607-dc-endpoint-anubis/` (ISSUE-LOG #740, wq#2607) and
`ops/wq1691.../` (ISSUE-LOG #740, wq#1691/seshat).

## Root cause: why the 2026-09-18 un-retire didn't touch these two units

`nephthys-mcp-failover.service` (:3100) and its `cloudflared` tunnel were
never a "paused, waiting to come back" pair — wq#589 (2026-08-23, Frank
directive "shut it down") explicitly **relocated the role**, not just
stopped the units:

> `wq589.notes`: "Nephthys decommissioned. Awareness->Bes
> (bes-awareness.service), MCP-failover->Bes (bes-mcp-failover.service, CF
> tunnel 1eaa5126), LiteLLM rungs removed, watchdog+benches updated, powered
> down, plug p2 cut."

So as of 2026-08-23 the *primary* MCP failover target is Bes, on purpose,
and Nephthys's own copies were left disabled by design, not by oversight.

The 2026-09-18/20 "un-retire" (`lumen_state` `nephthys.availability`: "ACTIVE
LLM local-worker/test-bench, un-retired 2026-09-18 / live since 09-20") is a
**different, unrelated role**: bringing Nephthys back as an LLM local-worker
inference rung for the dispatch rebuild (wq#2127 "found it up serving
qwen3:14b for LiteLLM local-worker", under the wq#2241/2253 inference-node
add/retire design). That path only touches LLM-serving targets — gateway
`litellm_config.yaml`, the scoped key allowlist, `dispatcher/config.json`,
`research/models.yaml`, `LOCAL_POOL_MODELS` (`harness/node_lifecycle.py:9`,
the "five targets" comment) — it has no step that reads wq#589's decommission
record or enumerates *other* units that existed on the node before
decommission. `nephthys-mcp-failover`/`cloudflared` were completely outside
that ticket's scope, so nobody looked at them, and they silently stayed dead
by config for a month until this ticket's 2026-09-25 audit found them.

**REPEAT=DEFECT root cause, one line**: un-retiring a node is done
role-by-role (whichever role the new ticket needs), with no step that
diffs the node's current `systemctl list-unit-files` against what was
disabled at its own decommission time — so any other role bundled into that
decommission (there were three at once for Nephthys: awareness, mcp-failover,
LLM-serving) stays dark unless a *separate* ticket happens to audit it.
Recorded in `../../docs/fleet/UN_RETIRE_CHECKLIST.md` (new doc — no such
checklist existed in this repo before this ticket; `grep -rl checklist
docs/ research/ ops/` turned up nothing about node lifecycle) and in
`kb/ISSUE-LOG.md`.

## Why this worker could not run it

This BUILD lane has no execution path to Nephthys at all:

```
$ /home/lumen/bin/fleet-exec grant
fleet-exec: DENY [grant:grant_missing_env]: grant file did not verify
```

Per ISSUE-LOG #740 (wq#1691/seshat precedent): `FOREMAN_GRANT_FILE` is only
populated at spawn time when the ticket carries a `needs:ssh(<node>)` tag
(`harness/fworker.py` `resolve_exec_grant`) — `wq#2609`'s own `needs` column
is empty (`sqlite3 agents/work-queue/queue.db "SELECT needs FROM tasks WHERE
id=2609"` -> empty string), so no grant was ever minted for this attempt.

Even a tag would not have been enough: `fleet-exec-policy.yaml:174`'s
`allow.ssh` list is `[lumen, isis, bes, anubis, maat, sobek, hathor]` —
**nephthys is not in it**, the same missing-from-policy gap as seshat in
ISSUE-LOG #740. Two independent gaps, both structural, neither fixable from
inside this ticket (widening a remote-exec policy unilaterally, without the
node being reachable to verify the change against, is exactly the kind of
unreviewed blast-radius expansion instinct 3's "make the call" carve-out
does not cover — this is a policy-file change, not a task decision).

There is also no plain `ssh` in this lane's Bash allowlist
(`harness/worker-settings.json`) — instinct 1's "SSH-wrap everything" assumes
a lane that has `ssh` allowed at all; this one only has the `fleet-exec`
broker, which is the path that's denied above.

`curl` against the *public* tunnel endpoint is allowed (it's just an
outbound HTTPS call), and was run for real from this session:

```
$ curl -s -o /dev/null -w "%{http_code}\n" https://mcp-failover.lumennode.uk/mcp
403
$ curl -s -o /dev/null -w "%{http_code}\n" https://mcp-failover.lumennode.uk/health
403
```

Both 403 — consistent with Cloudflare Access blocking an unauthenticated
request, but this does **not** prove the origin is up: Access returns 403
before ever reaching a dead origin too, so this is not the "cloudflared is
enabled and healthy" proof the ticket asks for. `verify.sh`'s authenticated
branch (needs the existing Access service token, never hardcoded here) is
the one that would actually distinguish those two cases, and that also
requires the token, which lives outside this worker's `--add-dir` set
(`/home/lumen/.secrets/**` is deny-listed for Read in
`harness/worker-settings.json`).

## What's in this directory

| file | purpose |
|---|---|
| `apply-nephthys.sh` | run as root on Nephthys: pins `supergateway@3.4.3` + `@wonderwhy-er/desktop-commander@0.2.46` (same pins as wq#2607's Anubis rollout), `systemctl enable --now nephthys-mcp-failover cloudflared`, local `/health` + `tools/list` probes, public 403 probe, then installs the wq#2606 T2 watchdog against this node by setting `WQ2606_GATEWAY_UNIT=nephthys-mcp-failover WQ2606_GATEWAY_PORT=3100` and calling `../wq2606-mcp-gateway-watchdog-a1/apply.sh` unmodified — that script was built node-agnostic for exactly this rollout (its own header: "ships unmodified to Anubis/Bes/Nephthys"). No new watchdog code needed; this is wiring, not a rewrite. |
| `verify.sh` | read-only: unauthenticated 403 checks against the public tunnel (safe from any network) plus an authenticated `tools/list` check gated on `CF_ACCESS_CLIENT_ID`/`CF_ACCESS_CLIENT_SECRET` being exported by the caller. |

## Exact remaining commands (for a lane with real access to Nephthys, e.g. `ssh nephthys@100.98.74.87` — reconfirm the tailscale IP via `lumen_state.py current nephthys tailscale_ip` first, addresses rotate)

```bash
scp ops/wq2609-nephthys-mcp-failover-a1/apply-nephthys.sh nephthys@<ip>:/tmp/
ssh nephthys@<ip> -- 'sudo bash /tmp/apply-nephthys.sh'
bash ops/wq2609-nephthys-mcp-failover-a1/verify.sh   # from anywhere, after exporting the Access token env vars
python3 /home/lumen/minerva/lumen_state.py ingest nephthys mcp_failover enabled --source wq2609
python3 /home/lumen/minerva/lumen_state.py regen
```

(`lumen_state.py ingest`/`regen` also needs real minerva access this lane
doesn't have per wq#2607's precedent — `minerva/context` is outside every
worker grant, wq#1467.)

## What this ticket actually closes today

- Root cause for the REPEAT=DEFECT ask, evidence-cited against wq#589's own
  notes field and the wq#2127/2241/2253 un-retire path, not guessed.
- `docs/fleet/UN_RETIRE_CHECKLIST.md`: the first checklist of its kind in
  this repo, with the cross-check step this incident was missing.
- The complete staged deploy (`apply-nephthys.sh`) + verify (`verify.sh`)
  scripts, reusing wq#2606's already-built, already-node-agnostic T2
  watchdog rather than duplicating it.
- `kb/ISSUE-LOG.md`: this ticket's instance of the no-remote-exec-path gap
  (same class as #740), plus the REPEAT=DEFECT root-cause entry.
- Confirmed via live `curl`: the public tunnel is currently 403 on both
  `/mcp` and `/health` (Access-level, inconclusive on origin health) —
  the actual enable/verify/ingest steps above remain to run on a lane with
  Nephthys access.
