## Summary

Expands wq#3335's Cloudflare-only `cf-changelog-watch` into a general provider-watch
system (wq#3336): one script, one config table of sources, four pluggable source
types (`rss`, `npm`/`pypi`, `github_releases`, `sitemap`), covering Cloudflare,
Tailscale, Ollama, LiteLLM, Cerebras, DeepSeek, SambaNova, Mistral, OpenRouter,
Langfuse, and PiKVM. Deployed live at `/home/lumen/bin/provider-watch` (byte-identical
to the committed `provider_watch.py`); ships a daily `.timer`/`.service` pair for an
operator with root to install (sandboxed workers can't write `/etc/systemd/system` —
see `ops/wq3336-provider-watch/DESIGN.md`).

Every source URL was curl-verified live before being hardcoded (several providers —
Cerebras, DeepSeek, OpenRouter — have no RSS feed at the paths you'd guess, or return
a misleading 200 `text/html` SPA shell for any path; full verification notes and
citations are in `DESIGN.md`). The ticket named `PiKVM/kvmd-armbian`, which doesn't
exist on GitHub — documented the correction to `pikvm/kvmd` in `DESIGN.md` and
`kb/ISSUE-LOG.md` row #1145.

Also found and fixed a latent bug in wq#3335's original first-run baseline logic
(`first_run = not os.path.exists(state_path)` doesn't actually catch a *corrupt*
state file, so it would flood-file one ticket per historical match instead of
re-baselining) — fixed in `provider_watch.py`, pinned by a test, and logged against
the still-live `cf-changelog-watch` for a follow-up fix (`kb/ISSUE-LOG.md` row #1146;
couldn't hot-patch it directly — `/home/lumen/bin` is guard-blocked for in-place Edit
on existing live files).

## Test plan

- [x] `python3 -m pytest ops/wq3336-provider-watch/test_provider_watch.py -q` — 22/22 pass
- [x] Live dry-run against all 11 non-Cloudflare sources: 347 matches, 0 source errors, baseline seeded
- [x] Second live run confirms dedup: 0 new on an unchanged fetch
- [x] Live dry-run against Cloudflare sources alone: 761 first-run matches, matching wq#3335's own documented count exactly
- [x] `/home/lumen/bin/provider-watch` verified byte-identical to the committed source and runnable (`list-sources`, `run --dry-run`)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
