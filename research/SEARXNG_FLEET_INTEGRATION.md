# SearXNG Fleet Integration — Research Report (wq#994)

**Author:** wq994-searxng session (Claude, Lumen architect)
**Date:** 2026-08-27
**Feeds:** wq#993 (build Reddit/GitHub/forum search tooling) → wq#991 (agents don't search before acting)
**Status:** COMPLETE — recommendation is **GO, with a corrected design**. All findings below were validated empirically on Lumen's own egress IP with an ephemeral SearXNG container (now torn down), not taken from third-party claims.

---

## 0. TL;DR / Go-No-Go

**GO** — deploy SearXNG as a fleet service, but the design that ships is *not* the one the ticket assumed. Three of the ticket's premises were wrong or stale and the empirical test corrected them:

1. **"Google blocks SearXNG; Reddit/GitHub/HN don't."** Inverted. From Lumen's **residential** Sonic IP, Google + Bing + DuckDuckGo each returned a full page of results with **zero** blocks. The engines that failed were Brave and Startpage (Brave needs a paid key since Feb 2026; Startpage rejects the instance).
2. **"Reddit `.json` — no auth needed, no rate limits at our volume."** Dead. Reddit began returning **403 to unauthenticated `.json` on 30 May 2026** (TLS-fingerprint + IP-reputation based). Verified live: `www.reddit.com/...json` → **403** from our own IP. **`old.reddit.com` HTML → 200** from our IP, and trafilatura extracted the exact community finding from it.
3. **"GitHub is blocked on wq#526 (no PAT)."** Stale. `GITHUB_TOKEN` in `/home/lumen/agent/.env` is **live** — 5000 core req/hr, 30 search/min, and it found `0alex1010/Freeko` and `openmiko/openmiko` directly. GitHub needs the **authenticated API, not SearXNG**.

**The correct architecture is a router, not a single meta-search:** SearXNG owns the *general-web / discovery* layer (which is what actually surfaces Reddit threads and niche forums); official APIs own GitHub, Hacker News, and StackOverflow; Reddit *comment bodies* come from a fetch-and-extract step on `old.reddit.com`. Meta-search finds the thread; extraction gets the value.

**Ops burden:** low. One container + one small `fleet-search` CLI + trafilatura (already validated). The Docker host already runs ~35 containers; marginal cost is ~375 MB image + ~150 MB RAM.

---

## 1. What the fleet is trying to fix (wq#991 recap)

Four documented incidents (Miko 3, RK3568 tablet, Google Nest, Meta headset) where Frank found the answer in ~3 searches and agents burned days. Root cause: agents (a) don't search, (b) search badly, (c) hit crawl blocks and give up, or (d) never look in Reddit/GitHub comments where hardware-RE findings actually live.

A structural contributor found this session, worth recording on its own: **the foreman workers have no web-search tool at all.** `worker-settings.json` grants `Read/Grep/Glob/Edit/Write`, a handful of `Bash(...)` verbs, and `Bash(curl *)` — but **not** `WebSearch` or `WebFetch`. So a worker literally cannot search the web through the CLI's native tools; its only egress is `curl`. That is a large part of "(a) don't search at all" and it means a curl-reachable local endpoint is the *natural* integration surface — no new tool type required.

---

## 2. Empirical test (this session, Lumen egress IP)

Ephemeral container: `searxng/searxng:latest`, bound `127.0.0.1:8888`, `search.formats: [html, json]`, `server.limiter: false`, own `secret_key`. Egress IP = **23.93.159.94 / AS46375 Sonic Telecom** (residential California ISP — this is the single most important variable and it is a genuine fleet advantage).

**Engine reachability** (`format=json`, one engine at a time, then with a query that has hits):

| Engine | Result | Notes |
|---|---|---|
| google | **10 results** | no block, no CAPTCHA |
| bing | **10 results** | works |
| duckduckgo | **10 results** | works |
| brave | 0 (unresponsive) | needs paid API key (free tier removed Feb 2026) |
| startpage | 0 (unresponsive) | instance rejected |
| github | **30** on `python asyncio` | engine works; niche queries return 0 |
| hackernews | **30** on `python asyncio` | engine works |
| stackoverflow | **10** on `python asyncio` | engine works |
| wikidata | init 403 | one dead engine; does not affect others |

**General search reproduced Frank's Miko find.** Query `miko 3 robot firmware` (default engines) → result #2 was the exact thread `reddit.com/r/hacking/comments/1r7oxdm/miko_3_robot/`, snippet already carrying a **comment fragment** ("...no published firmware for these anywhere and no EDL package..."). The dedicated `reddit` engine is *not* what surfaced it — general Google/Bing/DDG did. This matters: you don't need to un-break SearXNG's finicky reddit engine; the general layer already finds Reddit threads.

**Answer to the ticket's explicit test — "comments or just titles?":** neither, at the search layer. SearXNG returns title + URL + a **~155-character snippet** per Reddit result. Enough to *rank* a thread as relevant; not the payload. **The full comment requires fetching the URL.** Content extraction is therefore **mandatory, not optional** — confirmed.

**Reddit fetch + extraction, end to end, from our IP:**

| URL | HTTP | Result |
|---|---|---|
| `www.reddit.com/.../miko_3_robot/.json` | **403** | unauth `.json` dead (May 2026) |
| `old.reddit.com/.../miko_3_robot/` | **200, 198 KB** | full thread HTML |

`trafilatura 2.2.0` on the `old.reddit.com` HTML extracted the **entire top comment** — the complete jailbreak method (internal OTG USB port → keyboard → Win+N → dev tools → sideload APKs) **and the repo link `github.com/0alex1010/Freeko`**. That is exactly the wq#991 "Freeko / OpenMiko cloud-bypass already solved" finding. **The whole pipeline works: discover (SearXNG) → fetch (old.reddit, residential IP) → extract (trafilatura).**

Limitation observed: trafilatura got the top-level comment but missed a *deeper nested reply* (the "EDL / dd of block devices" one visible in the SearXNG snippet). trafilatura is tuned for main-content + top comments, not Reddit's nested tree. For thorough comment capture, a Reddit-aware comment-tree parser (BeautifulSoup over `old.reddit` `.thing .usertext-body`) beats trafilatura. For "surface the one key finding," trafilatura already suffices.

**Latency:** general search (google+bing+ddg aggregate) measured **0.26–0.47 s** across 3 samples — negligible for agent use.

**GitHub API direct (authenticated, token already present):** `Freeko miko` → `0alex1010/Freeko` (total 1, exact); `openmiko` → `openmiko/openmiko`; `miko3` → 16 repos. The community repos are one authenticated call away — no meta-search, no scraping.

---

## 3. Deployment config (validated this session)

Deploy the **official `searxng/searxng` image directly**. Do **not** deploy Vane. (Vane = Perplexica, rebranded March 2026 by ItzCrazyKns; it's the full Next.js answering-engine frontend that *bundles* SearXNG. We want only the SearXNG core. The `BunsDev/perplexica-search-engine-ai` fork the ticket cites is just a stale-named Perplexica fork — no reason to prefer it over upstream `searxng/searxng`.)

Recommended placement: a small compose stack of its own, bound to **localhost only** (agents reach it via `curl http://127.0.0.1:8888`, never exposed to the tailnet/CF). Valkey optional — with the limiter off for a single-user instance it isn't needed; add it only if we later want the limiter on.

`/home/lumen/searxng/docker-compose.yml`:
```yaml
services:
  searxng:
    image: searxng/searxng:latest
    container_name: searxng
    restart: unless-stopped
    ports:
      - "127.0.0.1:8888:8080"      # localhost only — never tailnet/CF
    volumes:
      - ./settings.yml:/etc/searxng/settings.yml:ro
    environment:
      - SEARXNG_SECRET=${SEARXNG_SECRET}
    mem_limit: 512m
    logging: { driver: json-file, options: { max-size: "10m", max-file: "3" } }
```

`/home/lumen/searxng/settings.yml` (the exact file tested — **the JSON + limiter lines are the whole ballgame**):
```yaml
use_default_settings: true
server:
  secret_key: "<32-byte hex — generate once>"
  limiter: false            # OFF: single-user private instance. NEVER off on a public one.
  public_instance: false
  image_proxy: false
search:
  safe_search: 0
  formats:
    - html
    - json                  # <-- OFF BY DEFAULT. Without this, format=json returns 403.
```

**The #1 gotcha, by a wide margin** (SearXNG docs + every integration writeup + reproduced here): JSON is disabled by default. `search.formats` must include `json` or every `format=json` request is a flat **403 Forbidden by design**, and you must restart after editing. The #2 gotcha: the built-in **limiter** treats an agent like a scraper (429/empty) — off for single-user. Both are set correctly above.

Health/smoke after deploy:
```bash
curl -s "http://127.0.0.1:8888/search?q=test&format=json" | python3 -c "import sys,json;print('OK',len(json.load(sys.stdin)['results']))"
```

## 4. Engine selection

Enable, per the empirical table: **google, bing, duckduckgo** (general/discovery — these do the real work), plus **github, hackernews, stackoverflow** engines as cheap redundancy. **Disable** brave and startpage (dead without keys) to stop them adding latency and noise. Keep the rest of the default set — SearXNG degrades gracefully when an engine goes quiet, so leaving extras on is harmless. Check `/stats` occasionally and prune whatever is erroring.

Rationale: the value is the **general aggregation layer**, because that is what surfaces Reddit/forum/blog threads (proven with the Miko find). The niche engines matter far less than the ticket assumed, because for the three sources where a niche engine exists *and* an official API exists (GitHub, HN, SO), the **official API wins** (§5).

---

## 5. Source-by-source access strategy (the actual design)

This is the core correction. There is no single best tool; there is a **router** that sends each source to its best-quality reachable path:

| Source | Best path | Why | Auth |
|---|---|---|---|
| **General web / blogs / forums / discovery** | **SearXNG** `format=json` (google+bing+ddg) | Aggregates, no key, surfaces Reddit/forum threads; validated | none |
| **Reddit — find threads** | SearXNG general search (`site:reddit.com` optional) | General engines already surface threads | none |
| **Reddit — read comments** | `curl old.reddit.com/<thread>/` (residential IP) → extract | `.json` is 403 since May 2026; `old.reddit` HTML is 200 from our IP | none (fragile) |
| **GitHub** repos/issues/PRs/discussions/code | **authenticated GitHub REST/GraphQL API** | token already live; returns full comment bodies, not 155-char snippets; found `Freeko`/`openmiko` directly | `GITHUB_TOKEN` (present) |
| **Hacker News** | **HN Algolia API** `hn.algolia.com/api/v1/search` | free, official, returns stories **and comments**; no scraping | none |
| **StackOverflow** | **StackExchange API** `api.stackexchange.com/2.3` | free, official; add a free key for 10k/day | optional key |

**Reddit is the only genuinely hard source, and it is hard for everyone in 2026.** The unauthenticated `.json` era ended 30 May 2026 (Reddit monetizing its corpus via Google/OpenAI licensing deals; enforcement is TLS-fingerprint + IP-reputation, so datacenter IPs are worst-hit). Our mitigations, in order:

1. **Residential IP + low volume + `old.reddit.com` HTML** — works today (proven), because the surviving niche is exactly "the occasional single request," which is the fleet's usage pattern. **This is the recommended primary Reddit path.** It is best-effort and could degrade; treat it as such (fail soft, never block a project on it).
2. **Official Reddit Data API (OAuth/PRAW)** — sanctioned, free for non-commercial ~100 req/min. *Caveat:* sources conflict on whether **self-service app registration is still open** (some report it closed Nov 2025, others report instant registration still works mid-2026). This needs a one-time verification by attempting registration — a Frank credential act (queued as a physical task, §11), not a blocker for everything else.
3. **Managed hosted layer** (Prowlo ~$19/mo flat, Apify, Scrapfly) — only if 1 and 2 both fail and Reddit becomes load-bearing. Not recommended to buy now.

## 6. Harness integration design

Because workers already have `Bash(curl *)` and nothing else web-facing, the integration is a **single curl-reachable CLI** the worker calls, not a new tool type. Build `/home/lumen/bin/fleet-search` (Python, stdlib + trafilatura):

```
fleet-search web "<query>" [--n 10]        # SearXNG JSON → ranked title/url/snippet list
fleet-search reddit "<query>"              # SearXNG site:reddit.com → threads
fleet-search read <url>                    # fetch + trafilatura extract (old.reddit-aware)
fleet-search github "<query>" [--issues]   # authenticated GitHub API (repos/issues/discussions)
fleet-search hn "<query>"                  # HN Algolia (stories + comments)
fleet-search so "<query>"                  # StackExchange API
```

Design rules:
- **Fail soft, attribute the source.** Every sub-command returns structured JSON with a `source`/`ok`/`error` envelope. A dead engine or a Reddit 403 returns `ok:false` with a reason — never a silent empty that reads as "no findings." (This is the same silent-fallback pathology called out in the memoryd work: a fail-open path that hides which side served makes a broken integration look healthy.)
- **One dependency add:** `pip install --break-system-packages trafilatura` (2.2.0, validated). bs4 already present for the Reddit comment-tree parser.
- **Allowlist:** `fleet-search` is already runnable under the current worker settings (it's invoked as `python3 /home/lumen/bin/fleet-search ...` and `Bash(python3 *)` is allowed). If we want it as a first-class verb, add `Bash(/home/lumen/bin/fleet-search *)` to `worker-settings.json` `allow`. **Do not** add raw `WebSearch`/`WebFetch` — routing everything through `fleet-search` keeps egress observable and cache-able.

**Alternative worth noting:** LiteLLM (already running, `localhost:4000`) has **native SearXNG support** — set `SEARXNG_API_BASE` and it exposes `/v1/search/searxng-search`. This is a zero-new-code way to give the gateway a search route. Recommendation: still build `fleet-search` as the canonical surface (it also does GitHub/HN/SO/extraction, which LiteLLM's route does not), but wiring `SEARXNG_API_BASE` into LiteLLM as well is a cheap bonus for any LiteLLM-native caller.

---

## 7. Content extraction

- **General pages / blogs / forums:** `trafilatura.extract(html, include_comments=True, favor_recall=True)`. Validated 2.2.0; clean main-content + top-level comments.
- **Reddit:** fetch `old.reddit.com` (not `www`, not `.json`). trafilatura gets the top comment (proven — pulled the full Freeko method). For **complete nested threads**, add a small `old.reddit` parser over `.thing .usertext-body` with depth via `.child` nesting — bs4 is already installed. Ship trafilatura first (covers the 80% case), add the tree parser in a follow-up if agents report missing deep replies.
- **GitHub / HN / SO:** no HTML extraction — the APIs return structured JSON with full bodies. Extraction is only for the open web + Reddit.

## 8. Research memory (per-project cache)

Land findings in the project dir so agents don't re-search: `/home/lumen/projects/<name>/research/PRIOR_ART.md` (human-readable) + a sibling `search_cache.jsonl` (query → results → fetched-and-extracted content, timestamped). `fleet-search` writes the cache on every call and checks it first (TTL ~14 days for volatile web, indefinite for a resolved hardware-RE finding). This directly satisfies wq#991 deliverable #5 and wq#993 #5. Wire the existing research indexer over `projects/*/research/` so prior art is retrievable via Minerva recall.

**This is also the enforcement hook for wq#991 #1 (mandatory research phase):** a hardware-RE project's `.fworker-rules.yaml` requires `PRIOR_ART.md` to exist and be non-empty before any device-interaction action is allowed — a phase gate, not a suggestion.

## 9. Paid fallback — economics (go/no-go)

The ticket asks whether Exa/Tavily (~$0.01/search) is worth it. Verified 2026 pricing: **Exa $7/1k** ($1/1k surcharge per result past the 10th) with a **20,000 searches/month free tier**; **Tavily ~$8/1k** with **1,000 credits/month free** (advanced = 2 credits, returns extracted content in one call); Serper $1/1k (cheapest raw Google, but Google-v-SerpAPI lawsuit spillover risk); Brave free tier removed Feb 2026; Bing Search API retired by Microsoft Aug 2025.

**Verdict: do not buy now; the free tiers already cover fleet volume at $0.** The fleet runs a handful of hardware-RE projects, not thousands of queries/day. Exa's 20k/month free tier alone would never be touched. More importantly, **the empirical test removed the reason to pay**: Google works from our residential IP via SearXNG, so the "Google-specific results" gap the paid fallback was meant to fill doesn't exist here.

Keep **one** managed API as a *documented, un-provisioned backstop* for the day the residential IP gets flagged or Reddit `old.reddit` closes: **Exa** (biggest free tier, code-search endpoint useful for RE) or **Tavily** (search+extract in one call, drops into the harness cleanly). Provisioning either is a one-time Frank credential act — queue it, don't gate on it, don't spend until a real block is observed.

## 10. Ops burden

Low. One `restart: unless-stopped` container (375 MB image, ≤512 MB RAM cap) on a host already running ~35; one ~200-line `fleet-search` CLI; one pip dependency (trafilatura, validated). No new exposed surface (localhost bind). Maintenance = occasional `/stats` engine prune + `docker pull` on the pinned image. The only *fragile* piece is the Reddit `old.reddit` path, and it's fail-soft by design. Estimated build effort for wq#993: ~half a focused session (deploy is minutes; the CLI + cache + rules-gate is the work).

---

## 11. Go/No-Go recommendation

**GO.** Build order for wq#993:

1. Deploy `searxng/searxng` (compose in §3), localhost-bound, JSON on, limiter off. Smoke with the §3 curl.
2. Build `/home/lumen/bin/fleet-search` with the six sub-commands (§6), fail-soft + source-attributed, per-project cache (§8).
3. GitHub/HN/SO via official APIs; general web + Reddit-discovery via SearXNG; Reddit-comments via `old.reddit` fetch + trafilatura (+ bs4 tree parser follow-up).
4. Add `Bash(/home/lumen/bin/fleet-search *)` to `worker-settings.json` allow.
5. Wire the `.fworker-rules.yaml` research-phase gate for hardware-RE projects (satisfies wq#991 #1).
6. Optionally set `SEARXNG_API_BASE` in LiteLLM for a native gateway route.

**Do NOT:** deploy Vane/Perplexica; rely on Reddit `.json`; build a dedicated SearXNG reddit engine; buy a paid search API before a real block is observed; expose SearXNG beyond localhost.

### Frank-only physical/credential tasks (queued, non-blocking — full autonomy on everything else)
- **[P1]** Verify whether Reddit Data API self-service app registration is still open; if so, mint OAuth creds on a throwaway/Frank account for the sanctioned Reddit fallback. (Sources conflict; needs a human attempt.)
- **[P3]** Optional: create an Exa or Tavily account for the un-provisioned managed-search backstop. Only if/when the residential path degrades.

## 12. Tickets seeded / to update

- **wq#993** — update detail: GitHub PAT is **live** (not blocked on wq#526); Reddit `.json` is **dead** (403 since May 2026), use `old.reddit` fetch; adopt the router design above; deploy `searxng/searxng` not Vane.
- **New build ticket** — deploy SearXNG fleet service (compose §3) + `fleet-search` CLI. (child of #993)
- **New build ticket** — `.fworker-rules.yaml` research-phase gate for hardware-RE projects (wq#991 #1 enforcement).
- **New follow-up ticket** — `old.reddit` nested-comment-tree parser (bs4) to complement trafilatura.
- **New user-lane ticket** — Frank: verify/mint Reddit OAuth app creds (P1 above).

## 13. Sources
SearXNG official docs (search_api, settings, limiter); reproduced live on Lumen 2026-08-27. Reddit `.json` deprecation (multiple 2026 writeups; enforcement began 30 May 2026). Exa/Tavily/Serper/Brave 2026 pricing (multiple comparison writeups, cross-checked). Vane = Perplexica rebrand March 2026 (ItzCrazyKns/Vane). LiteLLM SearXNG search provider docs. All version/price/availability claims are 2026-current; recheck before spend.
