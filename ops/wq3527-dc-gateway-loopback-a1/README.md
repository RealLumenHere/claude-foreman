# wq#3527 — dc-gateway :3100 loopback lockdown (horus, likely neith/khnum)

## Inventory (step 1 of the ticket, done from this BUILD lane's local scope)

Searched every dir this lane can read (worktree, `/home/lumen/bin`,
`/home/lumen/foreman`, `/home/lumen/kb`, `/home/lumen/agents/work-queue`,
`/home/lumen/mailbot`, `/home/lumen/pokemon`) for anything addressing
`<node>:3100` over LAN/tailnet rather than loopback:

- `/home/lumen/bin/mcpw-stateful-cutover.sh:24-26` — health/session-id probes
  against `:3100`, but all three hit `http://localhost:3100`, not a
  tailnet/LAN address. Loopback-only; unaffected by this change.
- `/home/lumen/bin/dc-redeploy.sh:35,37` — same, `ss -ltn | grep :3100` run
  locally on whichever box it executes on, not a remote probe.
- No hit for `horus:3100`, `neith:3100`, `khnum:3100`, or any bare tailnet/LAN
  IP against port 3100 in any script, Gatus config, or mcpw-diag source
  reachable from this lane.

`/home/lumen/sentinel/` (Gatus-equivalent probe config, per wq#2611/wq#897
precedent) and any gatus unit are **not** under this worktree or any
`--add-dir` root for this ticket, so they could not be grepped from here.
Likewise `mcpw-diag` (if it is a separate tool from the `mcpw-*` scripts
above) was not found under any readable root. Both are gaps in this lane's
inventory, not confirmed clear — flag to whoever applies the config change
below to grep those two before flipping the bind, per the ticket's explicit
"before binding 127.0.0.1" ordering.

## Why the fix is staged, not applied

```
$ /home/lumen/bin/fleet-exec grant
fleet-exec: DENY [grant:grant_missing_env]
```

`wq#3527`'s `needs` column is empty — no `ssh(horus)` (or neith/khnum) grant
was minted for this attempt. This is the same structural gap
`kb/ISSUE-LOG.md` #740/#754/#1064 already logged for Seshat/Nephthys/Bes:
this BUILD lane's only remote-exec path is the `fleet-exec` broker, which is
denied, and there is no bare `ssh` on this lane's Bash allowlist. horus's
`/etc/systemd/system/dc-gateway.service.d/30-mcp-proxy.conf` is not mirrored
under this repo or any `--add-dir` root, so there is also no local file to
diff against — matching wq#2611's bes-awareness gap exactly.

## What to apply, once a session has real horus (and neith/khnum) access

1. `systemctl cat dc-gateway.service` on horus to get the live ExecStart.
2. Confirm it is mcp-proxy (not supergateway — supergateway 3.4.3 has no
   `--host` flag at all, see `kb/ISSUE-LOG.md` #838; if the equivalent node
   is still on supergateway, this `--host` flip does not apply there and
   needs the actual fix tracked separately).
3. Copy that ExecStart into `30-mcp-proxy.conf` in this directory, changing
   only `--host 0.0.0.0` -> `--host 127.0.0.1` (never `::`, see the comment
   in that file — asyncio's IPV6_V6ONLY on a dual-stack bind breaks the
   127.0.0.1 health/watchdog checks that must keep working).
4. `systemctl daemon-reload && systemctl restart dc-gateway`, then verify
   from horus itself: `curl -fsS http://127.0.0.1:3100/health` succeeds, and
   from another tailnet node: a request to `horus:3100` (or its tailnet IP)
   times out / connection-refused instead of answering.
5. Repeat the inventory for neith and khnum (ticket's "likely") — find their
   dc-gateway unit, confirm it's mcp-proxy with the same `0.0.0.0` bind, and
   apply the same drop-in pattern per node.
