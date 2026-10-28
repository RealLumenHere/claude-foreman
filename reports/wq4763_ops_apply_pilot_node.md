# wq#4763 — B2-T0 ops-apply pilot node verification: NEITHER NODE QUALIFIES

**Verdict: no qualifying pilot node.** bes's tunnel fronts the Commander path; anubis has no
tunnel at all. B2-T0 blocks per DESIGN.md §3.2's own precondition ("If neither passes, B2-T0
blocks with the finding").

Verified 2026-10-28 via read-only `fleet-exec run` (needs:ssh), no mutation. All reads below are
re-runnable with the commands shown.

## bes — DISQUALIFIED: tunnel fronts the Commander/desktop-commander endpoint

`fleet-exec run bes cat /etc/cloudflared/config.yml` (filtered to ingress lines; credentials-file
contents never read, only the key name observed):

- Top-level keys: `tunnel`, `credentials-file`, `no-autoupdate`, `ingress`.
- Exactly **one** ingress hostname (grep -c `hostname:` = 1):
  - `mcp-failover.lumennode.uk` → `http://localhost:3100`
  - final catch-all `service: http_status:404`
- No `vault.*` ingress. ✔ on the Vault half of the check.

`fleet-exec run bes systemctl status cloudflared`: active (running) since 2026-10-20, Main PID
5263, `/usr/bin/cloudflared --no-autoupdate --config /etc/cloudflared/config.yml tunnel run`.

**The disqualifier** — `fleet-exec run bes systemctl cat bes-mcp-failover.service`:

> Description=Bes MCP Failover — **Desktop Commander via Supergateway (backup for Lumen
> dc-gateway)**
> ExecStart=.../supergateway --stdio ".../desktop-commander" **--port 3100** --outputTransport
> streamableHttp --streamableHttpPath /mcp ...

Port 3100 is exactly what the tunnel's only ingress fronts. bes's sole ingress IS the
Commander/desktop-commander endpoint (the wq#2608 DC-endpoint deployment, ISSUE-LOG row 742).
DESIGN.md §3.2's precondition fails on bes: the tunnel fronts the Commander path. Worse for the
pilot's safety story: any `cloudflared.ingress.upsert` recipe run on bes restarts/reloads the
tunnel that carries the Commander failover path — the exact instinct-6 surface the precondition
exists to keep out of unattended change.

## anubis — DISQUALIFIED: no tunnel exists

- `fleet-exec run anubis cat /etc/cloudflared/config.yml` → `No such file or directory`
- `fleet-exec run anubis ls /etc/cloudflared` → `No such file or directory`
- `fleet-exec run anubis systemctl status cloudflared` → `Unit cloudflared.service could not be found.`
- `systemctl list-units --type=service --all` filtered for cloudflared/tunnel/mcp → no matches.

This matches ISSUE-LOG row 740 (wq#2607): the Anubis DC endpoint + own-tunnel work was built as
artifacts in `ops/wq2607-dc-endpoint-anubis/` and recorded "**prepared not deployed**"; it was
never applied. DESIGN.md §3.2's fallback assumption ("anubis (own tunnel, wq#2607)") is stale —
there is no tunnel for `cloudflared.ingress.upsert` to operate on. And the staged wq#2607 design
is itself a desktop-commander endpoint, so deploying it as-staged would reproduce bes's
disqualification, not cure it.

## What would unblock B2-T0 (for the design doc, not this ticket)

1. **Different node**: pick a node with a cloudflared tunnel that fronts only non-Commander,
   non-Vault services — none exists today among grant-reachable nodes per these reads (bes = DC
   failover; anubis/no tunnel; lumen/sobek excluded by `never.restart`, maat/thor excluded by
   §3.2).
2. **New throwaway tunnel**: stand up a dedicated pilot tunnel on anubis fronting a dummy
   loopback service (not DC) — a deploy ticket, out of scope for this read-only verify.
3. **Revisit the precondition**: Frank-level call; §3.2 explicitly reserves it.

Option 2 is the natural next ticket: it creates the non-crown-jewel tunnel §3.2's pilot needs
without touching the Commander path.
