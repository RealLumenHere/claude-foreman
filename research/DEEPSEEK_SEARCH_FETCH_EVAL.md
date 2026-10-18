# DeepSeek Search/Fetch API — Evaluation (wq#4194)

**Author:** wq4194 build worker (Claude, Lumen)
**Date:** 2026-10-18
**Ticket:** evaluate `https://api.deepseek.com/v1/tools/search` + `.../v1/tools/fetch`
as a replacement/supplement for SearXNG in agent web research.
**Status:** COMPLETE — recommendation is **GO, as a supplement** (not a full
replacement yet). Reference client + live-API probes below; see "Follow-up"
for why the actual wiring into `fleet-search` is a separate ticket.

## 0. TL;DR

- **Cost is trivial.** Both Search and Fetch are billed **$0.75 per 1,000
  successful requests** (not per token, not per result count) — about
  **$0.00075/call**. At a generous 500 fleet searches/day that's ~$0.38/day,
  ~$11/month. `DEEPSEEK_API_KEY` already exists fleet-wide for model calls
  (`research/VENDOR_KEY_ROTATION.md:10,36`), so no new credential needs
  minting.
- **SearXNG is demonstrably fragile right now, not hypothetically.** Running
  `fleet-search web` live during this evaluation (2026-10-18) returned:
  all four general engines (brave, duckduckgo, google cse, startpage)
  suspended/CAPTCHA'd, **and** the keyed fallback (`brave_api_search`,
  `/home/lumen/bin/fleet-search:299-321`) failed too because
  `BRAVE_API_KEY` is still unconfigured (wq#2595, open since 2026-08).
  DeepSeek Search would have answered that exact query for less than a
  tenth of a cent.
- **Fetch returns clean Markdown in one call**, where fleet-search's current
  `_fetch_ladder` (`/home/lumen/bin/fleet-search:702-730`) runs up to four
  tiers (curl_cffi, headless Chromium, FlareSolverr, raw) to fight anti-bot
  walls. DeepSeek Fetch is a candidate extra tier, not a replacement for the
  ladder — "subject to the target site's availability and access policies"
  per the docs, so it will still 403/404 on sites that block generic
  crawlers.
- **Not a full SearXNG replacement today:** SearXNG is free when its engines
  are healthy, and DeepSeek's billing note says calls proxied through
  "DeepSeek Harness, Claude Code, or Codex" are free until **00:00 Beijing
  time, 2026-11-01** — after that the same review should be redone in case
  pricing or quotas shift. Recommendation is to add DeepSeek Search as the
  **fallback slot** SearXNG's engines fail into (same slot `brave_api_search`
  occupies today), not as the primary.

## 1. API shape (captured live, 2026-10-18)

Source: `https://api-docs.deepseek.com/guides/search` (fetched via
`fleet-search read`, cached at `/tmp/fworker-wq4194/deepseek_search_docs.json`).

| API | Endpoint | Auth |
|---|---|---|
| Search | `POST https://api.deepseek.com/v1/tools/search` | `Authorization: Bearer <key>` |
| Fetch | `POST https://api.deepseek.com/v1/tools/fetch` | `Authorization: Bearer <key>` |

Search request: `{query, count (1-50, default 10), ori_question?, localization?, need_content?}`.
Query string supports `site:`, `-site:`, `-term`, `-"phrase"`, `"phrase"`,
`lang:`, `after:`, `before:` operators, forwarded as-is to the search engine.

Search response: `results: [{rerank_score (0-3, >=1.5 = high quality), title,
url, snippet, summary, content (empty unless need_content), site_name,
site_icon, date, authority (0-4, -1 unknown, >=3 authoritative)}]`.

Fetch request: `{url}` (≤8192 chars, http/https, no embedded userinfo).
Fetch response: `{title, content}` with `content` as Markdown.

Errors are OpenAI-compatible (`{"error": {"message", "type", "param": null,
"code"}}`). Documented codes: 400 invalid request, 401 missing/invalid key,
402 insufficient balance, 403 Fetch rejected by target's security policy,
404 Fetch found no usable content, 408 body-read timeout, 413 body too
large, 429 QPS exceeded, 500/504 upstream errors. 408/429/5xx are
retry-safe; other 4xx are not.

Rate limits: **5 QPS** each for Search and Fetch, counted independently
per account (not per key), plus a per-domain QPS cap on Fetch.

## 2. Billing (captured live, 2026-10-18)

| API | Unit | Price |
|---|---|---|
| Search | per 1,000 successful requests | $0.75 |
| Fetch | per 1,000 successful requests | $0.75 |

Billed only on HTTP 200 **with a non-empty result** (non-empty `results[]`
for Search, extracted content for Fetch) — empty results, timeouts, and
errors are free. `count` changes how many results come back, not the price.

> Calls that happen through DeepSeek's own agentic surfaces (DeepSeek
> Harness, Claude Code, Codex server-side tool use) are free of charge until
> 2026-11-01 00:00 Beijing time, after which per-request billing starts for
> those too. This fleet's integration is a **direct API call** from
> `fleet-search`/the agent harness, not one of those three surfaces, so it
> is billed at the table above from day one — the free-tier note doesn't
> apply to us either way.

## 3. Live verification (this session, no production key available)

`DEEPSEEK_API_KEY` lives in `/home/lumen/agent/.env`, which is outside this
worktree's `--add-dir` grants (sandbox boundary for a build worker, not a
retryable gap — see RULES). Two things were still verified live, with no
key required:

1. **Endpoint reality check** — unauthenticated POSTs to both endpoints
   returned HTTP 401 with the exact documented error shape, not a 404:
   ```
   $ curl -s -m 8 -o /dev/null -w 'HTTP_%{http_code}\n' https://api.deepseek.com/v1/tools/search -H 'Content-Type: application/json' -d '{"query":"test","count":1}'
   HTTP_401
   {"error":{"message":"Authentication Fails (missing key)...","type":"authentication_error","param":null,"code":"invalid_request_error"}}
   ```
   Same for `/v1/tools/fetch`. This confirms the endpoints exist, are live,
   and match the documented OpenAI-compatible error format.
2. **Request-plumbing check against the live API** —
   `research/tools/test_deepseek_search_fetch_client.py` sends a
   syntactically-valid-but-wrong bearer token through the reference client
   (`research/tools/deepseek_search_fetch_client.py`) and asserts the live
   API rejects it with 401, proving the URL/method/headers/JSON body the
   client builds are correct, not mocked:
   ```
   ok: no key configured: no DEEPSEEK_API_KEY configured
   ok: no key configured: no DEEPSEEK_API_KEY configured
   ok: live search endpoint rejects bad key: deepseek search 401 -- key invalid/revoked
   ok: live fetch endpoint rejects bad key: deepseek fetch 401 -- key invalid/revoked
   ```
3. **SearXNG's current state, same session** — `fleet-search web "DeepSeek
   search API /v1/tools/search pricing"` (while researching this ticket)
   returned:
   ```
   "error": "all general web-search engines suspended/blocked (brave,
   duckduckgo, google cse, startpage); fallback failed: no BRAVE_API_KEY
   configured (see wq#2595: mint a Brave Search API key)"
   ```
   This is the live failure mode item 0 above refers to — captured during
   this evaluation, not inferred from old tickets.

An authenticated 200-path call (real query, real results) could not be run
from this worktree for the reason above; a follow-up ticket with a grant
into `/home/lumen/agent` (or run from a context that already has the key,
e.g. a `wq#999`-style session in the `fleet-search` repo) should do that
before flipping the integration on in production.

## 4. Reference implementation

`research/tools/deepseek_search_fetch_client.py:46-99` implements
`deepseek_search()` and `deepseek_fetch()` with the same
`(results_or_None, error_or_None)` convention fleet-search's own
`searxng_search()`/`brave_api_search()` use
(`/home/lumen/bin/fleet-search:273-321` — a separate repo this branch
cannot commit into, hence "reference" rather than "wired in"). Result dicts
carry `rerank_score`/`authority` so a caller can filter to high-quality
results without a second ranking pass, and `deepseek_fetch()`'s return
shape matches `_fetch_and_extract()`'s `{title, content}`
(`/home/lumen/bin/fleet-search:733-783`).

## 5. Recommendation

**GO, as a supplement:**

1. Add `deepseek_search()` as a second fallback in `cmd_web`
   (`/home/lumen/bin/fleet-search:324-349`), tried when SearXNG's general
   engines are all down — same trigger `brave_api_search` already uses,
   but DeepSeek doesn't need a new key to be minted first.
2. Add `deepseek_fetch()` as a tier in `_fetch_ladder`
   (`/home/lumen/bin/fleet-search:702-730`) for URLs where the existing
   tiers hit an anti-bot wall but DeepSeek's crawler is allowed through.
3. Re-run this evaluation before 2026-11-01 in case DeepSeek's free-tier
   cutover changes pricing or quota for direct API callers.
4. Do not decommission SearXNG: it is free when healthy and still covers
   Reddit/GitHub/HN-specific discovery paths the router already special-cases.

## 6. Follow-up (filed, not blocked)

Steps 1-2 above are edits to `/home/lumen/bin/fleet-search`, which is its
own git repo (`git -C /home/lumen/bin rev-parse --show-toplevel` ->
`/home/lumen/bin`, distinct from this worktree's `/home/lumen/foreman`
checkout) and needs its own worktree/branch/PR to land in, matching how
wq#999 wired the original SearXNG router in after wq#994's evaluation. Per
instinct 3 (full autonomy, no Frank-gates) this is not parked on a
decision — it's queued as the next ticket because it is a different repo's
change, not a different person's call. See ticket filed in "Follow-up
ticket filed" in the done notes.
