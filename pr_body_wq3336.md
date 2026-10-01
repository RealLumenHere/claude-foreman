## What

Expands wq#3335's Cloudflare-only `cf-changelog-watch` into a general provider-watch
system (wq#3336): `ops/wq3336-provider-watch/provider_watch.py`, one config table of
sources (`SOURCES`), four pluggable source types (`rss`, `npm`/`pypi`,
`github_releases`, `sitemap`), covering Cloudflare, Tailscale, Ollama, LiteLLM,
Cerebras, DeepSeek, SambaNova, Mistral, OpenRouter, Langfuse, and PiKVM. Also ships:
`test_provider_watch.py` (22 tests), `DESIGN.md` (source verification notes + what's
deployed vs. pending), `provider-watch.service`/`.timer`, and `install.sh`.

Deployed live at `/home/lumen/bin/provider-watch`, byte-identical to the committed
script, verified runnable. The systemd timer is **not** installed — see Risk/rollback.

## Why

wq#3335 built the Cloudflare-only version after we missed the official MCP server
stack for months while hand-rolling a broken OAuth implementation. The same failure
mode applies to every provider we build against — a feature ships, we don't hear
about it, and weeks later we discover we hand-rolled something that already existed.
wq#3336 asks for the general case across 11 providers rather than one.

## How

A single dispatcher (`findings_for_source`) routes each configured source to its
type-specific fetcher; dedup (sha256 of source+identifier), first-run baseline (don't
flood on day one — a cold Cloudflare run alone matches 761 historical entries), and
per-source error isolation (one dead feed doesn't kill the sweep) are unchanged from
wq#3335's design, applied generically across source types instead of hardcoded to
RSS+npm.

Every source URL was curl-verified live before being hardcoded — several providers
(Cerebras, DeepSeek, OpenRouter) have no RSS feed at the paths you'd guess, or return
a misleading 200 `text/html` SPA shell for any path including wrong ones, so status
code alone wasn't enough; content-type and a content peek caught it. Full citations
in `DESIGN.md`. The ticket named `PiKVM/kvmd-armbian`, which doesn't exist on GitHub
(`github.com/pikvm`'s 13 repos don't include it) — corrected to `pikvm/kvmd`,
documented in `DESIGN.md` and `kb/ISSUE-LOG.md` row #1145.

`provider-watch`'s `SOURCES` table includes Cloudflare (so the tool is genuinely
general-purpose, dry-run verified against live Cloudflare sources: 761 first-run
matches, exactly matching wq#3335's own documented count), but `DEFAULT_PROVIDERS`
(used by the unattended timer) excludes it, since `cf-changelog-watch.timer` is still
live and already covers it under a separate state file — running both would file
duplicate tickets.

Along the way, found and fixed a latent bug in wq#3335's first-run logic:
`first_run = not os.path.exists(state_path)` doesn't catch a *corrupt-but-present*
state file, so instead of re-baselining it would file one ticket per historical match
— the exact flood the baseline step exists to prevent. Fixed here by gating
`first_run` on whether `load_state()` actually produced a `"seen"` key. Logged against
the still-live `cf-changelog-watch` (`kb/ISSUE-LOG.md` row #1146) for a follow-up fix
there — couldn't hot-patch it directly; `/home/lumen/bin` is guard-blocked for
in-place Edit on existing live files (wq#1719/wq#900), by design.

## Testing

- `python3 -m pytest ops/wq3336-provider-watch/test_provider_watch.py -q` — 22/22 pass, covering every source-type fetcher, relevance tiering, dispatch, first-run baseline, dedup, source-error accounting, the corrupt-state-file fix, and the CLI.
- Live dry-run against all 11 non-Cloudflare sources: 347 matches, 0 source errors, baseline seeded.
- Second live run against the same state file confirms dedup: 0 new.
- Live dry-run against Cloudflare sources alone: 761 first-run matches, matching wq#3335's own documented count.
- `/home/lumen/bin/provider-watch` verified byte-identical to the committed source (`diff` exit 0) and runnable (`list-sources`, `run --dry-run`).

## Risk/rollback

Read-only against every provider's public endpoints; the only writes are a JSON state
file and, on a new match, one `wq add` subprocess call — same blast radius as the
already-running `cf-changelog-watch`. Rollback is reverting this commit and deleting
`/home/lumen/bin/provider-watch`.

The daily timer is **not installed** — this worker's sandbox has no path to
`/etc/systemd/system` (not an `--add-dir`; `sudo`/`systemctl enable` are deny-listed
verbs for workers). `install.sh` in `ops/wq3336-provider-watch/` does the install
(copy + `daemon-reload` + `enable --now`) and needs one run by an operator with root
on Lumen — queued as a follow-up physical task, not a gate on this landing.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
