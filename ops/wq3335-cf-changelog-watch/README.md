# wq#3335 — cf-changelog-watch

We missed the official MCP server stack (`workers-oauth-provider`, `McpAgent`,
CF Access SaaS MCP) for months while hand-rolling a broken OAuth
implementation. This watches Cloudflare's own release surfaces daily and
files a wq ticket on anything new that touches an area that's burned us
before — MCP/OAuth/Workers/Agents/Access/KV/Durable Objects/tunnel/cloudflared.

- **Sources** (verified live 2026-10-01 — the documented
  `/changelog/rss.xml` 301-redirects to the real feed, so this hits the
  redirect target directly):
  - CF blog RSS: `https://blog.cloudflare.com/rss`
  - CF changelog RSS: `https://developers.cloudflare.com/changelog/rss/index.xml`
  - watched npm packages via the registry's own `<pkg>/latest` endpoint (no
    npm CLI dependency): `@cloudflare/workers-oauth-provider`,
    `@cloudflare/agents`.
- **Keyword filter** (RSS only — a watched npm package is relevant by the
  mere fact it moved): `MCP`, `OAuth`, `Workers`, `Agents`, `Access`, `KV`,
  `Durable Objects`, `tunnel`, `cloudflared`. Matched case-insensitively on
  word boundaries against title + description.
- **Relevance assessment**: `high` for MCP/OAuth matches (the exact gap that
  cost us months), `medium` for Access/Agents/Workers/Durable Objects or any
  npm version bump, `low` for any other watched keyword. Written into the
  filed ticket's detail alongside the link and matched keywords.
- **On a new match**: `wq add --lane research --pri p3 --title "[cf-changelog]
  <source>: <title>" --detail "..."`. `wq add`'s own duplicate-title check is
  a second line of defense; the primary dedup is this tool's own state file.
- **Dedup**: `sha256(source + guid-or-link)` for RSS items,
  `sha256("npm" + pkg + version)` for npm — written to a seen-hashes state
  file (`/home/lumen/logs/.cf-changelog-watch.state.json`) so a run never
  refiles something it already filed.
- **First-run bootstrap**: a live smoke test found the changelog RSS carries
  ~760 historical matches, not just recent posts. A cold run (no state file
  yet) seeds everything it currently sees as "already seen" and files
  nothing — only items that show up in a *later* run alert. A missing or
  corrupt state file always reads as "first run" (same degrade-to-empty path
  `load_state` already has), so losing the state file re-baselines instead of
  re-flooding the queue.
- **Read-only against Cloudflare/npm**: the only writes are the state file
  and, on a new match, one `wq add`.
- **Runs**: `cf-changelog-watch.timer`, daily (`OnBootSec=15min
  OnUnitActiveSec=24h`), as the `lumen` user.

## Install / rollback

```
lumen-run start --root -- bash /home/lumen/foreman/ops/wq3335-cf-changelog-watch/install.sh
/home/lumen/bin/cf-changelog-watch run --dry-run
```

`install.sh --verify` answers the wq#1696 deploy-verify contract read-only:

```
bash /home/lumen/foreman/ops/wq3335-cf-changelog-watch/install.sh --verify
```

Rollback: `systemctl disable --now cf-changelog-watch.timer`, remove
`/etc/systemd/system/cf-changelog-watch.{service,timer}`,
`/home/lumen/bin/cf-changelog-watch`, and the state file. Nothing else is
mutated.

## Tests

`python3 -m unittest discover -s ops/wq3335-cf-changelog-watch` — fully
offline: every network call (`fetch_url`, `wq add`) is injected/stubbed.
