# wq#3445 triage: CF blog "Everything we launched during Birthday Week 2026"

Source: https://blog.cloudflare.com/birthday-week-2026-wrap-up/ (2026-10-05), filed by
cf-changelog-watch (wq#3335). Read via `fleet-search read` on 2026-10-05 (HTTP 200, 46 entries).

## Verdict: informational, no action required. Close.

This is a roundup post. Each item has its own blog post, and cf-changelog-watch files those
separately when they match (Streamline is already wq#3435). Nothing in the roundup changes or
deprecates anything we run.

What we run on Cloudflare, checked 2026-10-05:
- `mailbot/wrangler.toml` binds `send_email`, D1 (`MAIL`), R2 (`QUARANTINE`) and a cron
  trigger. It has no KV, no Durable Objects and no `observability` block.
- No `wrangler.*` in foreman, agent, pokemon or this repo binds KV or Durable Objects.
- No code in foreman/agent/mailbot/bin calls `gateway.ai.cloudflare`, Workers AI or `@cf/`
  models. Our LLM gateway is LiteLLM, not CF AI Gateway.
- No script in bin/foreman runs `cloudflared tunnel --url` or uses `trycloudflare`.

## Items that touch our stack, and why none needs work now

| Announcement | Relevance |
|---|---|
| Protected Quick Tunnels (email auth on Quick Tunnels, no CF account for viewers) | We don't use Quick Tunnels. ISSUE-LOG row 359 (wq#1804) applies if we ever do: `cloudflared tunnel --url` loads `~/.cloudflared/config.yml` and runs as our named tunnel unless config is isolated. |
| Workers KV Instant (sub-2 ms p99 reads, Quicksilver-backed, same KV API) | We have no KV bindings. Re-read if the KV cap from wq#991 comes back. |
| 8 Observability updates / Cloudflare Traces ("simpler pricing") | mailbot's wrangler.toml has no `observability` block, so we don't opt into Workers Logs and the pricing change doesn't reach us. |
| Issues: Workers errors sent to coding agents or webhooks | Possibly useful for mailbot error routing into wq. It would need a webhook receiver. Not filed; mailbot error volume doesn't justify it. |
| AI Gateway Auto Router / User Insights / Web Search API | We route through LiteLLM, not CF AI Gateway. N/A. |
| `cf` agentic CLI for the whole CF API | Alternative to wrangler + raw API calls. No gap it would fill today. |
| Web Crypto ML-KEM / ML-DSA in Workers (opt-in) | mailbot does no custom crypto. N/A. |
| Containers rebuilt for agent sandboxes, Cloudflare OS, Artifacts, K2, Basin, AI Search GA, Clef | New products with no current fleet consumer. |

The rest (open source: EmDash, Vinext, VoidZero, Forge, Kitesurf, BEACON, Rust/Emscripten;
PQ CA and MTC, IPsec downgrade, Application Profiles, WAF tests, Threat Signals; Monetization
Gateway, Pay Per Use, domains; accessibility, OHTTP Gateway, account-abuse dashboard, network
performance, Impact) is outside our footprint.
