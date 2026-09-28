# Fleet SearXNG in Frank's Claude Desktop (wq#2485)

**Status: NOT LIVE YET — gated on wq#3069.** The fleet SearXNG (container `vane` on
Lumen) binds `127.0.0.1:8888` only (verified 2026-09-28: `/proc/net/tcp` shows the
sole 8888 listener on loopback; `curl http://100.117.66.102:8888` from Lumen itself →
`ECONNREFUSED`). wq#3069 exposes it on Lumen's Tailscale interface; nothing below
changes when it lands — the URL is chosen to match either exposure option there.

Everything Frank-side is prepared and tested as far as this host allows. Once wq#3069
closes and the URL verifies from a non-lumen node, paste and go.

## The URL

```
http://100.117.66.102:8888
```

`100.117.66.102` is Lumen's Tailscale IP (verified assigned to this host by a local
socket bind test, 2026-09-28). Tailnet-only — reachable from Thor because both ride
the tailnet; never exposed publicly (`server.limiter: false`, single-user instance —
public exposure is forbidden, research/SEARXNG_FLEET_INTEGRATION.md:99).

## Ready-to-paste `claude_desktop_config.json` block

Merge into the `mcpServers` object (Claude Desktop → Settings → Developer → Edit
Config, or `%APPDATA%\Claude\` / `~/Library/Application Support/Claude/` /
`~/.config/Claude/` depending on OS), then fully quit and reopen Claude Desktop:

```json
{
  "mcpServers": {
    "searxng": {
      "command": "npx",
      "args": ["-y", "mcp-searxng"],
      "env": { "SEARXNG_URL": "http://100.117.66.102:8888" }
    }
  }
}
```

Needs Node/npx on the workstation. The MCP server exposes `searxng_web_search` (plus
a URL-reader tool); ask Claude Desktop to "search the web for X" and check the tool
inventory shows `searxng`.

## Package note — ticket said `searxng-mcp`, this ships `mcp-searxng`

Both exist on npm (checked against the registry 2026-09-28):

- `searxng-mcp` v1.1.0 — configures via `SEARXNG_SERVER_URL` (its README's only
  URL-shaped env var).
- `mcp-searxng` v2.4.0 (ihor-sokoliuk) — configures via `SEARXNG_URL`, ships the exact
  Claude Desktop `npx -y` recipe above in its README.

The ticket's own spec ("npx -y …, `SEARXNG_URL` env") describes `mcp-searxng`; the
name in the ticket text was a transposition. Using `searxng-mcp` with `SEARXNG_URL`
would silently fall back to its default instance — wrong results, no error.

## Smoke test (Frank, from Thor, before touching the config)

```bash
curl -s "http://100.117.66.102:8888/search?q=test&format=json" | head -c 200
```

- **JSON with a `results` array** → good, paste the config block.
- **403** → SearXNG's `search.formats` lost `json` (it was verified present:
  loopback probe returned HTTP 200 with results on 2026-09-28, and it is required
  config — research/SEARXNG_FLEET_INTEGRATION.md:106-109). File a ticket.
- **Connection refused / timeout** → wq#3069 hasn't landed (or regressed).

## Scope guard (per ticket)

- Frank's workstation config only. No fleet agent gets an MCP server from this work:
  agents keep `bin/fleet-search` (14-day cache, project scoping, fail-soft envelope)
  and avoid the frozen-toolset / prompt-cache-prefix problem
  (research/PREFIX_STABILITY.md).
- No fleet settings.json, toolset, or server config is modified by this doc.
