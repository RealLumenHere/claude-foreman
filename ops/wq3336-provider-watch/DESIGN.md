# wq#3336 — provider-watch: generalized changelog/release monitor

## Problem

wq#3335 built `cf-changelog-watch` for Cloudflare alone after we missed the official
MCP server stack (workers-oauth-provider, McpAgent, CF Access SaaS MCP) for months
while hand-rolling a broken OAuth implementation. The same failure mode applies to
every other provider we build against: a feature ships, we don't hear about it, and
weeks later we discover we hand-rolled something that already existed. wq#3336 asks
for the general case: Cloudflare, Tailscale, Ollama, LiteLLM, Cerebras, DeepSeek,
SambaNova, Mistral, OpenRouter, Langfuse, PiKVM.

## What shipped

One script, `provider_watch.py` (installed live as `/home/lumen/bin/provider-watch`),
replacing the single-provider shape of `cf-changelog-watch` with a config table of
sources (`SOURCES` in the script) and a small dispatcher over four source types:

| type              | what it does                                              |
|-------------------|------------------------------------------------------------|
| `rss`             | RSS 2.0 feed; optional keyword filter+tier (Cloudflare only) |
| `npm` / `pypi`    | registry "latest version" lookup, finding iff version is new |
| `github_releases` | GitHub REST `/repos/{repo}/releases/latest`, finding iff tag is new |
| `sitemap`         | `sitemap.xml` `<loc>` entries matching a required path filter, for providers with no RSS/Releases surface |

Dedup, first-run baseline (don't flood on day one), and per-source error isolation
(one dead feed doesn't kill the sweep) are unchanged from wq#3335's design — see the
module docstring in `provider_watch.py` for the exact behavior and the corrupt-state
fix made along the way (see "Bug fixed" below).

## Source verification (2026-10-01 — see provider_watch.py docstring for citations)

Every URL below was verified live before being hardcoded, per house doctrine ("never
guess a path/URL, verify it"):

- **Cloudflare** — unchanged from wq#3335: blog RSS, changelog RSS, 2 npm packages.
- **Tailscale** — blog RSS (`tailscale.com/blog/index.xml`, 200/text-xml) + GitHub
  releases (`tailscale/tailscale`, has Releases).
- **Ollama** — GitHub releases (`ollama/ollama`, has Releases).
- **LiteLLM** — GitHub releases (`BerriAI/litellm`) + PyPI (`litellm`).
- **Cerebras** — **no RSS feed exists** (confirmed via fleet-search: "Cerebras Blog
  has no RSS feed of its own" — feeder.co builds a synthetic one by scraping the
  page). Fell back to `cerebras.ai/sitemap.xml` filtered to `/blog/` — the sitemap is
  real, 453 URLs, includes blog posts.
- **DeepSeek** — "blog/announcements" lives at `api-docs.deepseek.com/news/`, a
  client-rendered Docusaurus page with no RSS. Its `sitemap.xml` lists every
  `/news/newsYYMMDD` page as plain `<loc>` text, so that's the watched source
  (filtered to `/news/`).
- **SambaNova** — blog RSS at `sambanova.ai/blog/rss.xml` (confirmed real RSS 2.0,
  not a guess that happened to 200).
- **Mistral** — blog RSS at `mistral.ai/rss.xml`. Note: `/news/rss.xml` and
  `/feed.xml` both 404; only the bare `/rss.xml` path is real.
- **OpenRouter** — changelog RSS at `openrouter.ai/docs/changelog/rss.xml`, found via
  their own API-versioning docs page ("Subscribe to the RSS feed for changelog
  entries"). The guessed `/changelog/rss.xml` and `/rss.xml` paths both return 200
  `text/html` — an SPA shell, not a feed — so checking content-type/content, not just
  status code, mattered here.
- **Langfuse** — GitHub releases (`langfuse/langfuse`, has Releases).
- **PiKVM** — the ticket says "PiKVM/kvmd-armbian". **That repo does not exist.**
  `github.com/pikvm` has 13 repos (`pi-builder`, `kvmd`, `ustreamer`, `os`,
  `packages`, `pikvm`, `kvmd-auth-server`, `kvmd-cloud`, `kvmd-fan`, `linux`,
  `cloud-api`, `markdown-include`, `pidcodes.github.com`, `usb-pid`) — no
  `kvmd-armbian`. GitHub search turns up only an unrelated third-party fork
  (`xe5700/kvmd-armbian`). PiKVM's actual OS/armbian build repos (`pikvm/os`,
  `pikvm/pi-builder`) don't publish GitHub Releases (404 on `/releases/latest` —
  tag-only). `pikvm/kvmd`, the core daemon that actually ships the features we'd
  care about, does publish Releases, so that's what's watched.

All of the above were curl-verified live before being committed (status code +
content-type + a content peek where the status code alone was misleading — several
providers' SPA frameworks return 200 `text/html` for any path, including wrong ones).

## Why Cloudflare is excluded from the default unattended run

`cf-changelog-watch.timer` (wq#3335) is still live on this host and already covers
Cloudflare under its own state file (`/home/lumen/logs/.cf-changelog-watch.state.json`).
`provider-watch`'s `SOURCES` table *does* include Cloudflare — the code is fully
general and `provider-watch run --providers cloudflare` works end-to-end (dry-run
verified: matches wq#3335's own "761 historical entries" first-run count exactly) —
but `DEFAULT_PROVIDERS` (used when `--providers` is omitted, i.e. the unattended
timer) excludes it, specifically to avoid filing duplicate tickets for the same
Cloudflare items under two different dedup state files. If `cf-changelog-watch` is
ever retired, dropping `cloudflare` from the exclusion (or just always passing
`--providers` explicitly) is a one-line change.

## Bug fixed (found by the new test suite, inherited from wq#3335)

The original `first_run` check was `not os.path.exists(state_path)`. A state file
that exists but is corrupt (same bytes as a bad write, a truncated disk, etc.) would
fail that check, and the code would silently skip the "baseline everything, file
nothing" step the very next line's comment claims it does — it would instead treat
every historical match as new and file one ticket each, exactly the flood wq#3335's
own first-run logic was built to prevent. Fixed by gating `first_run` on whether
`load_state()` actually produced a `"seen"` key (which degrades to `{}`/absent for
*both* "missing" and "corrupt" files, matching the comment's original claim).
`test_run_corrupt_state_file_rebaselines_instead_of_reflooding` in
`test_provider_watch.py` pins this.

## What's deployed vs. what needs a privileged follow-up

Deployed in this change (both reachable from this worker's sandbox):
- `provider_watch.py` — committed here, the source of truth.
- `/home/lumen/bin/provider-watch` — a byte-identical copy, installed via the
  `--add-dir`-writable `/home/lumen/bin`, executable, ready to run standalone.

**Not deployed** — this worker's sandbox has no path to `/etc/systemd/system` (not an
`--add-dir`, `sudo`/`systemctl enable`/`daemon-reload` are deny-listed verbs), the
same way wq#3335's actual timer install must have happened outside a sandboxed
worker's reach. `provider-watch.service` / `provider-watch.timer` are committed here
ready to install; `install.sh` in this directory does the install (`install-script`
copy + `systemctl daemon-reload` + `enable --now`) and needs to be run once by an
operator (or a session) with root on Lumen. This is a physical/privileged act, not a
decision — queued as a follow-up, not a gate on this ticket landing.

## Manual verification run (2026-10-01)

```
$ python3 provider_watch.py run --state /tmp/.../smoketest.state.json --dry-run
provider-watch: [dry-run] first run — would seed 347 existing match(es) as baseline, file 0
# (all 12 non-Cloudflare sources fetched live, 0 source errors)

$ python3 provider_watch.py run --state /tmp/.../smoketest.state.json   # real run, seeds baseline
provider-watch: first run — seeded 347 existing match(es) as baseline, filed 0

$ python3 provider_watch.py run --state /tmp/.../smoketest.state.json --dry-run  # second run
provider-watch: 347 match(es), 0 new, 0/12 source(s) errored   # dedup confirmed

$ python3 provider_watch.py run --providers cloudflare --dry-run
provider-watch: [dry-run] first run — would seed 761 existing match(es) as baseline, file 0
# matches wq#3335's own documented 761-entry first-run count exactly
```
