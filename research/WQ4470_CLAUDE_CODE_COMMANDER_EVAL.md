# wq#4470: Claude Code CLI as Commander replacement — research

## Verdict: PARTIAL. One direction is already shipped, under a different name. The other direction is structurally blocked, and swapping CLIs would not touch the proxy chain the ticket wants gone.

The ticket conflates two different connection directions that Commander and Claude Code sit on
opposite ends of:

- **Orchestrator -> node** (something dispatches a task to a fleet machine and wants it done
  there directly): Claude Code headless already does this. It is not a research question; it
  is this fleet's live production mechanism.
- **claude.ai (browser) -> node** (a human or the Messages API's MCP connector, with no local
  process of its own, wants to reach into a specific fleet machine's filesystem/shell over the
  internet): Claude Code CLI cannot serve this role. It is an MCP *client/host*, not a
  network-reachable MCP *server*, and nothing in its design changes that.

## Headless + orchestrator-invoked: already live on this fleet, not a gap to close

Prior-art search (`fleet-search web "Claude Code CLI headless mode automation"`, 2026-10-23)
confirms `claude -p "<prompt>"` is Anthropic's documented headless entry point
(code.claude.com/docs/en/headless).

This fleet already runs on exactly that mechanism: `fworker.py:2555` spawns
`cmd = [claude_bin, "-p", prompt]` — "one `claude -p` child per attempt"
(`fworker.py:2`, docstring). This ticket itself is executing inside one of those children right
now: a headless Claude Code CLI session, invoked by the fworker orchestrator, running directly
on the target node with no Horus hop and no mcpw proxy anywhere on this path. For the
"orchestrator dispatches work to a node" direction, Claude Code already replaced any
Commander-style indirection, under the name `fworker`/`wq`, well before this ticket was filed.

## Tool set vs Desktop Commander: no real gap

Desktop Commander (wonderwhy-er, the package backing the fleet's `desktop-commander-mcp`
service — `docs/fleet/retired_services.yaml:86,104` in `FLEET_SERVICE_ATLAS.md`) exposes 26
tools centered on terminal execution, file read/write, ripgrep-backed search, and `edit_block`
diffs (prior-art search, mcp.so/servers/desktopcommandermcp and the Docker Hub listing).
Claude Code ships the direct functional equivalent natively — Bash, Read, Write, Edit, Grep,
Glob — with no MCP round-trip needed to use them. There is no missing capability on the
tool-set axis; this was never the binding constraint.

## Where the premise breaks: Claude Code is a client/host, not a remote-facing server

`claude mcp serve` exists, but prior-art search (code.claude.com/docs/en/mcp) is explicit that
it starts Claude as a **stdio** MCP server and that "this MCP server only exposes Claude Code's
tools to your MCP client" — a local subprocess handing its own tools to one local caller. It
has no built-in HTTP/SSE transport and no OAuth; it was built so another local MCP client can
embed Claude Code as a tool provider, not so a remote browser session can dial into a specific
machine over the internet.

Commander's actual job on this fleet is that second, opposite direction:
`docs/fleet/retired_services.yaml:325-327` — "Commander now runs on Horus (mcp-proxy), reached
via the mcpw.lumennode.uk Cloudflare Worker" — and `FLEET_SERVICE_ATLAS.md:86` names the
consumer directly: "**desktop-commander-mcp :3100** | claude.ai browser sessions". Reaching
that port from the public internet, authenticated, is why the chain has the shape it has:
`desktop-commander` (stdio) -> `supergateway` (stdio->HTTP, `FLEET_SERVICE_ATLAS.md:104`) ->
`cloudflared` tunnel -> `mcpw.lumennode.uk` Cloudflare Worker doing OAuth
(register/authorize/token/initialize, 7-day access-token TTL —
`foreman/ops/p1-commander/README.md:103-106`, `mcpw-diag.py`).

Swapping the stdio backend from `desktop-commander` to `claude mcp serve` would not remove a
single layer of that chain. `supergateway`, `cloudflared`, and the `mcpw` OAuth Worker are all
there to solve "make a local stdio MCP server safely reachable by an anonymous remote browser
client" — a requirement that is orthogonal to which CLI sits behind it. The proxy hop and its
timeout are a property of exposing *any* local stdio MCP server to claude.ai over the internet,
not a Desktop-Commander-specific defect that a different backend process would fix.

## The actual "eliminate the hop" work is already in flight, and it isn't Claude Code

`foreman/ops/p1-commander/` (wq#2744) replaces the single Horus chokepoint with two dedicated
nodes (`p1-primary`/`p1-backup`) that each run their own `dc-gateway` + `cloudflared` tunnel
directly on their own hostnames (`mcp-p1-primary.lumennode.uk`, `mcp-p1-backup.lumennode.uk` —
`README.md:22`), explicitly so that, once cut over, "Lumen leaves the control-plane path"
(`README.md:97-99`). That is the real fix for the single-hop/chokepoint complaint in the
ticket, and it keeps `desktop-commander` as the tool backend — Claude Code CLI would add
nothing to this plan, since the problem it solves (which box the tunnel terminates on) is
independent of which stdio MCP implementation sits behind that tunnel.

## Recommendation

- Do not install Claude Code CLI on fleet nodes as a Commander replacement.
- Orchestrator-initiated work: nothing to build. `fworker.py` already is this, today.
- claude.ai-browser-reaches-a-node direction: keep riding Commander's existing chain; let
  wq#2744's P1 pair land to remove the chokepoint-hop part of the complaint. Re-open this
  question only if Anthropic ships a hosted/remote transport for `claude mcp serve` itself —
  nothing in the current docs or this search suggests that exists.
