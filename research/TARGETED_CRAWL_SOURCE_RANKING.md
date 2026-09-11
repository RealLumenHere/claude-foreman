# Targeted Crawl + Source-Quality Ranking vs `github_scout` — Research Report (wq#1717)

**Author:** fworker-wq1717 (Claude Opus 5), worktree `wq1717-a1`
**Date:** 2026-09-11
**Proposal:** 0af07ae055ce
**Status:** COMPLETE — **PARTIAL ADOPT**. Take one narrow piece of the focused-crawl
pattern (depth-1 hub expansion), reject the general focused crawler, and invert the
source-quality-ranking proposal from a *ranker* into a *filter*.
**Feeds:** wq#994 (SearXNG / `fleet-search` router), wq#991 (agents don't search), wq#993

---

## 0. TL;DR

Three findings, each measured on live data today, decide this:

1. **`github_scout` is a popularity feed, not a discovery engine.** Of the 19 repos it
   queued on its 2026-09-10 run, the median had **36,860 stars**; 14 of 19 had >10k and
   8 of 19 had >50k. It queued gap-analysis on `open-webui`, `dify`, `litellm`, `gitea`,
   `coolify` — projects the fleet already runs. Cause is structural, not tuning: it sorts
   `(keyword_score, stars)` descending over `topic:X pushed:>7d stars:>50`
   (`lumen_scout_graph.py:147,151,194`).

2. **The scout's `SKIP_PATTERNS` discard the single highest-yield object on GitHub.**
   `"awesome-"` is a -5 penalty (`:67,:187-189`) and the keep threshold is `score >= 1`
   (`:207`), so curated lists are dropped by construction. Measured: three scout-shaped
   topic queries produced **30** candidates; the scorer dropped `punkpeye/awesome-mcp-servers`
   (94,773★, score **-2**); **one** fetch of that README yielded **3,868 distinct repos**.
   Across all five dropped candidates: **3,885 distinct repos, 3,882 of them absent from
   the candidate pool**, for **1.73 MB and 0.85 s** of network. That is a ~129× recall
   multiplier for under a second
   (`research/wq1717_hub_expansion_probe.json`, `research/tools/hub_expansion_probe.py`).

3. **Source-quality ranking, applied the way the proposal implies, makes results worse.**
   Re-sorting a 50-document relevance pool by citation count demoted the field's seminal
   paper from rank 1 to rank 5, behind two off-topic giants (§3.2). Relevance ranking with
   a well-specified query already put it first. Quality signals belong *after* relevance as
   a filter, never as the sort key.

**So:** the crawl literature's *frontier machinery* solves a problem we do not have (we
own three good indexes: GitHub API, SearXNG→Google/Bing/DDG, OpenAlex). Its *hub* insight
(Kleinberg's distillers, 1999) solves the problem we do have, and costs one HTTP request.
Adopt the hub hop; skip the crawler.

**Blocking prerequisite, worth more than either change:** `fleet-search github` runs
**unauthenticated** today — `--code` returns **401**, plain repo search reports
`"authenticated": false`, and the scout was **rate-limited on topic 11 of 14** on its last
run (`/home/lumen/agent/logs/scout_graph.log`, 2026-09-10 06:00:15). Fix that first; it is
a one-line credential repair that lifts search from 10/min to 30/min and unlocks code
search. See §6 and ISSUE-LOG row 325.

---

## 1. What is actually deployed (read, not assumed)

| Component | Path | What it does | State today |
|---|---|---|---|
| `github_scout` | `/home/lumen/autonomy/lumen_scout_graph.py` (330 lines, LangGraph) | 14 hardcoded `SEARCH_TOPICS` → `topic:X pushed:>7d stars:>50`, `sort=stars`, `per_page=10` → ≤140 candidates/day → keyword score → dedup → ≤30 gap-analysis prompts in `/home/lumen/scratch/research_prompts` | `lumen-scout.timer` **active**, daily 06:00 UTC, last run 2026-09-10, 81 s |
| `fleet-search` | `/home/lumen/bin/fleet-search` (551 lines) | the wq#994 router: `web` (SearXNG JSON), `reddit`, `github` (REST + GraphQL discussions), `hn`, `so`, `read` (fetch+trafilatura) | working; **GitHub leg unauthenticated** |
| SearXNG | localhost container | google+bing+ddg aggregation | working (0.26–0.47 s, per wq#994) |
| web-search MCP | — | **no MCP, and no `WebSearch`/`WebFetch` grant for workers** (ISSUE-LOG rows 321, 323) | `fleet-search` *is* the web-search surface |

Note for the proposal's framing: there is no "web-search-MCP". The fleet's web search is
`fleet-search web` over a local SearXNG. Any adoption lands as `fleet-search` subcommands,
not as a new MCP server.

### 1.1 The scout's ranking function, in full

```python
score = sum(1 for kw in RELEVANT_KEYWORDS if kw in combined)      # :184-186
score -= 5 * sum(1 for pat in SKIP_PATTERNS if pat in combined)   # :187-189
scored.sort(key=lambda r: (r["score"], r["stargazers_count"]), reverse=True)  # :194
if repo["relevance_score"] < 1: skip                               # :207
```

`combined` is `full_name + description + topics`, lowercased. Two consequences worth
naming, because both are load-bearing for the recommendation:

- Scoring is **substring counting over ~140 characters of metadata**. A repo's README,
  commit history, issue activity and dependents are never consulted. `"ai"` matches inside
  `chain`, `explain`, `domain`; `"api"` inside `rapid`. The top score observed on
  2026-09-10 was 17 — i.e. 17 of 36 keywords matched a one-line description, which tells
  you the score is measuring description verbosity as much as relevance.
- The tiebreak is **stars**, and since keyword scores cluster tightly (the probe's top five
  scored 10, 10, 10, 9, 8), stars are effectively the primary sort.

---

## 2. What focused crawling actually is, and what it buys

Citations verified live against OpenAlex today (`api.openalex.org/works?search=...`);
counts are that API's `cited_by_count`.

| Work | Year | Cites | Contribution | Relevant to us? |
|---|---|---|---|---|
| Chakrabarti, van den Berg, Dom — *Focused crawling: a new approach to topic-specific Web resource discovery* | 1999 | 1,495 | The canonical design: a **classifier** scores page relevance, a **distiller** identifies hub pages worth revisiting | **The distiller, yes. The classifier+frontier, no.** |
| Kleinberg — *Authoritative sources in a hyperlinked environment* (HITS) | 1999 | 9,102 | Hubs and authorities are distinct and mutually reinforcing; a good hub is worth more than a good page | **Yes — this is the whole recommendation** |
| Diligenti, Coetzee et al. — *Focused Crawling Using Context Graphs* | 2000 | 521 | Learns expected *distance* to relevant pages, so the crawler can tunnel through off-topic pages | No — tunneling is a multi-hop problem; we need one hop |
| Rennie & McCallum — *Using Reinforcement Learning to Spider the Web Efficiently* | 1999 | 212 | RL over link text to order the frontier | No — requires a frontier to order |
| Menczer, Pant et al. — *Topical web crawlers* | 2004 | 269 | Systematic evaluation: **naive best-first is competitive with or beats the elaborate adaptive crawlers** | Yes, as a warning: the sophistication does not pay |
| Gyöngyi, Garcia-Molina, Pedersen — *Combating Web Spam with TrustRank* | 2004 | 1,031 | Propagate trust from a hand-vetted seed set to demote spam | Partially — see §3.3 |
| Cormack, Clarke, Buettcher — *Reciprocal rank fusion outperforms Condorcet and individual rank learning methods* | 2009 | 685 | RRF: `sum(1/(k+rank_i))`, k≈60. No training, no score calibration, beats tuned alternatives | **Yes — this is how to combine our sources** |

**The structural point.** Every focused crawler in that table exists to *order a frontier*
— to decide which of N million unseen URLs to fetch next, in a corpus with no usable index.
We are not in that situation. We have the GitHub search API, a SearXNG meta-search over
three major engines, and OpenAlex. Our bottleneck is not "which link next"; it is "our
ranking function sorts by stars and throws away the curated lists." A focused crawler
would cost a frontier store, a politeness scheduler, `robots.txt` compliance, extraction,
dedup, and a trained relevance classifier — and Menczer 2004 says the trained classifier
would barely beat best-first anyway.

What survives the translation is Chakrabarti's **distiller** and Kleinberg's **hub**: the
observation that some pages are valuable *because of what they link to*. On GitHub that
object has a name, a naming convention, and 94,773 stars — and the scout is configured to
discard it.

---

## 3. Measurements

### 3.1 Hub expansion (the case to adopt)

`research/tools/hub_expansion_probe.py` reproduces the scout's query shape and scorer
verbatim, then does depth-1 expansion on what the scorer drops. Run unauthenticated,
matching the scout's real state. Full output: `research/wq1717_hub_expansion_probe.json`.

```
topics: mcp, ai-agent, llm-tools       candidates: 30       errors: []

scout keeps (its own sort):            score  stars
  n8n-io/n8n                             10   203,964
  langgenius/dify                        10   155,377
  HKUDS/nanobot                          10    48,006
  open-webui/open-webui                   9   151,581
  Graphify-Labs/graphify                  8   116,785

scout drops (score < 1):               score  stars
  bojieli/ai-agent-book                    0    45,745
  KhazP/vibe-coding-prompt-template        0     3,059
  punkpeye/awesome-mcp-servers            -2    94,773   <-- "awesome-" penalty
  CherryHQ/cherry-studio                  -2    51,659
  hugohe3/ppt-master                      -3    53,580

depth-1 expansion of the 5 dropped:
  hubs fetched                 5
  distinct repos           3,885   (punkpeye/awesome-mcp-servers alone: 3,868)
  not in candidate pool    3,882
  cost                  1.73 MB, 0.851 s
```

Cross-check on a different domain — the rank-1 result for `fleet-search github
"speculative decoding"` is `hemingkx/SpeculativeDecodingPapers`, a curated list. One fetch
of its README (124,977 bytes) yields **299 distinct arXiv ids, 96 distinct repos, 1,174
links**. The hub pattern is not an MCP-ecosystem artifact.

**Honest caveat, and it matters.** 3,868 links is a *candidate pool*, not 3,868 findings.
The expansion buys recall and nothing else; precision now has to come from somewhere. That
is exactly what makes the second half of the proposal worth doing — but only in the shape
§3.3 argues for, and only downstream of expansion.

### 3.2 Quality-as-ranker (the case to reject)

Reproducible in two calls. Same query, same 50-document pool, two sort orders:

```
curl -s "https://api.openalex.org/works?search=focused%20crawling%20topic%20specific%20web%20resource%20discovery&per-page=50&select=title,publication_year,cited_by_count"
```

| Rank | By API relevance | By citation count within the same pool |
|---|---|---|
| 1 | **Focused crawling: a new approach…** (1999, 1,495) | A survey of transfer learning (2016, 6,302) |
| 2 | Social media analytics (2017, 806) | LLaMA (2023, 3,970) |
| 3 | Information retrieval on the web (2000, 562) | From Frequency to Meaning (2010, 2,904) |
| 4 | Computing and applying trust (2005, 839) | ConceptNet 5.5 (2017, 2,126) |
| 5 | Database techniques for the WWW (1998, 562) | **Focused crawling: a new approach…** (1999, 1,495) |

Quality-sorting moved the one paper you actually wanted from first to fifth. Sorting the
*whole* corpus by citations is worse still — `search=focused crawler&sort=cited_by_count:desc`
returns Louvain community detection (21,717), Gephi (11,509) and *The Architecture of
Complexity* (4,511): nothing to do with crawling, because a popularity prior dominates a
weak relevance filter every time.

This is the same failure as the scout's `(score, stars)` sort, one domain over. **A
popularity signal used as a sort key does not find good things; it finds famous things.**

### 3.3 Where quality signals do belong

As a **filter and an annotation over an expanded pool**, which is precisely the position
TrustRank occupies in the literature (demote spam from a vetted seed), not the position a
re-sort occupies. All three verified reachable and free from Lumen today:

| Signal | Endpoint | Verified |
|---|---|---|
| OpenSSF Scorecard (maintained, code-review, CI, token-permissions…) | `api.securityscorecards.dev/projects/github.com/{o}/{r}` | **200**, 14.6 KB; `apache/nutch` = 5.3, `Maintained` 10, `Code-Review` 8 |
| ecosyste.ms repo metadata + dependent counts | `repos.ecosyste.ms/api/v1/hosts/GitHub/repositories/{o}%2F{r}` | **200**, 11.4 KB, 0.30 s, no key |
| OpenAlex (papers: venue, citations, concepts) | `api.openalex.org/works?search=…` | **200**, 0.43 s, no key, 28,336 hits on `focused crawler` |
| `pushed_at` / `archived` / `open_issues` | already in the GitHub search payload | free — no extra call |

Semantic Scholar was **429 on the first unauthenticated call** (174 B, 0.09 s). Treat it as
key-required; OpenAlex covers the same ground with no key and should be the default.

### 3.4 Query reformulation (the cheapest win of all)

GitHub's search is lexical, so an intent-shaped query returns literally nothing:

```
fleet-search github "tool that ranks open source projects by maintenance health instead of stars"
  -> {"ok": true, "results": [], "total": 0}

fleet-search github "repository health metrics"
  -> org-metrics-dashboard, kubevirt/ci-health, DSACMS/metrics, github-health-monitor-mcp, …
fleet-search github "open source project sustainability metrics CHAOSS"
  -> api-evangelist/chaoss
```

Same intent, 0 → 6 on-topic results, from nothing but keyword choice. And the reverse
holds on the paper side: the well-specified query in §3.2 put Chakrabarti 1999 at rank 1
with no reranking at all, where the vague `focused crawler` query (§3.3) did not surface
it in the top 3. **Most of what a "better ranker" is being asked to fix is a query
problem,** and an LLM caller is already the right thing to fix it — one cheap completion
emitting 3 keyword queries, fused with RRF.

---

## 4. Recommendation

**PARTIAL ADOPT.** Four changes, in cost order. None needs a crawler, a frontier, a
trained classifier, a vector index, or a new service.

### R1 — Fix GitHub auth (prerequisite, ~10 minutes)

`GITHUB_TOKEN` is absent or stale in `/home/lumen/agent/.env`: `fleet-search github --code`
→ 401, repo search → `"authenticated": false`, and the scout burned a 60 s rate-limit sleep
on topic 11 of 14 (81 s runtime for what should be ~15 s). Authenticated search is 30/min
vs 10/min and code search exists only with a token. **Every other recommendation here is
worth less than this one**, and the wq#994 report's claim that the token is live
(2026-08-27) no longer holds.

### R2 — Depth-1 hub expansion: `fleet-search hubs` (~half a session)

```
fleet-search hubs "<query>" [--max-hubs 3] [--since 180d]
```

1. Run the existing `github` repo search.
2. Identify hubs — name or description matching `awesome|curated|list|papers|collection`,
   or a README over ~20 KB with a link density above ~1 link per 200 bytes. (Link density
   is the robust test; the naming convention is the cheap one. Use both.)
3. Fetch `raw.githubusercontent.com/{repo}/{main,master}/README.md` for the top `--max-hubs`
   — one request each, no recursion, no `robots.txt` question (raw.githubusercontent is an
   API surface, and the content is the repo's own README).
4. Harvest `github.com/{owner}/{repo}` and `arxiv.org/(abs|pdf|html)/{id}`; dedup; return
   with `via_hub` provenance on every item.
5. **Hard-stop at depth 1.** The moment you follow a link *out of* a harvested README you
   have a crawler, with every cost §2 lists and the tunneling problem Diligenti 2000
   exists to solve. Depth 1 is where the cost/yield ratio is absurd in our favour; depth 2
   is where it inverts.

Reference implementation of steps 3–4 already exists and is tested:
`research/tools/hub_expansion_probe.py:expand_hub`.

### R3 — Quality as a filter stage, never a sort key (~half a session)

Applied **only** to an expanded pool (R2's output), never to re-sort a narrow relevance
list — §3.2 is the evidence for that restriction. Two tiers, because 3,868 candidates
cannot each get an API call:

- **Tier 1, free, no network:** drop `archived`; drop `pushed_at` older than `--since`;
  drop repos whose only evidence is a hub mention if they are under a low star floor
  (~10) — the cheap anti-spam pass that TrustRank's seed-demotion is the principled
  version of.
- **Tier 2, one call each, top ~30 survivors only:** OpenSSF Scorecard `Maintained` +
  `Code-Review`, ecosyste.ms dependent count. **Annotate, and emit the numbers in the
  JSON envelope.** Let the calling agent weigh them against its actual task; do not
  collapse them into a single score and sort by it. A research-grade candidate and a
  dependency-grade candidate want opposite weightings, and the ranker cannot know which
  one is being asked for.

### R4 — Multi-query + RRF fusion in `fleet-search` (~2 hours)

Accept `--queries q1 --queries q2 …` (or read newline-delimited queries on stdin) and fuse
result lists with `score(d) = sum_i 1/(60 + rank_i(d))` (Cormack 2009 — no training, no
score normalisation, nothing to tune). The caller is an LLM and is already the right
component to write three keyword spellings of one intent (§3.4). This also gives
`fleet-search` a principled way to merge GitHub + SearXNG + OpenAlex result lists, which
it currently cannot do at all.

### R5 — Rewire `github_scout` (~1 hour)

Three edits to `lumen_scout_graph.py`, each pointed at a measured failure:

1. Drop `"awesome-"` from `SKIP_PATTERNS` (`:67`) and route lists to R2 instead of
   discarding them. This is the 129× line.
2. Replace the `(keyword_score, stars)` sort (`:194`) with the **inverse**: among repos
   passing a relevance floor, prefer **low** stars and **recent** `pushed_at`. The fleet
   does not need to be told about `open-webui` at 151,581 stars — a 500-star repo solving
   a fleet problem is the entire point of a scout. An explicit star *ceiling*
   (`--max-stars`, default ~20k) would have removed 14 of the 19 results in the
   2026-09-10 run.
3. Keep the substring scorer as a **gate** (`score >= 1`, `:207`) and stop using it as a
   *rank*. It measures description verbosity (§1.1); it is fine as a coarse filter and
   misleading as an ordering.

### What NOT to build

- **A general focused crawler** (Apache Nutch, StormCrawler, ACHE, Scrapy, Firecrawl-style
  managed crawl). We have three indexes; the frontier machinery solves a problem we do not
  have, and Menczer 2004 says the trained variants barely beat best-first even where the
  problem *is* real.
- **Depth >1 traversal.** See R2 step 5.
- **A learned reranker / cross-encoder / LLM listwise reranker over search results.**
  §3.2 shows the ranking is not the binding constraint; §3.4 shows the query is. Revisit
  only if R2+R4 land and agents still report missing results.
- **A vector index over repos or papers.** Same reason, plus an embedding store to maintain.
- **Paid search or crawl APIs.** wq#994 §9 already concluded the free tiers cover fleet
  volume; nothing here changes that. R1 is the credential that matters.
- **Anything that sorts by a popularity signal.** That is the bug, in both domains, twice
  measured.

---

## 5. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Hub READMEs are large (1.6 MB observed) and blow a worker's 2000-char Bash cap / context | **high** | `fleet-search hubs` must return a capped, deduped JSON list with `--n`, never raw README text. Harvest server-side; the 1.6 MB never reaches a model. |
| 3,868-candidate pools flood the caller | high | R3 Tier 1 filter runs before return; default `--n 25`. |
| Hub link lists go stale / contain dead repos | medium | R3 Tier 1 `archived` + `pushed_at` filter catches these for free. |
| `raw.githubusercontent.com` rate-limits unauthenticated fetches | low | `--max-hubs 3` default; R1's token applies to raw fetches too. |
| Scorecard / ecosyste.ms unavailable | low | Both are annotations. Fail soft with `ok:false` per wq#994 §6 — never a silent empty. |
| Inverting the scout's star sort floods the queue with abandoned toys | medium | Keep the `score >= 1` gate *and* add the `pushed_at` recency floor; `MAX_NEW=30` already caps blast radius. Watch one week of output before widening. |
| R2 drifts into a crawler over time | medium | Depth-1 is an assertion in code, not a convention: `expand_hub` takes no depth parameter. Keep it that way. |

---

## 6. Tickets to seed

- **[P1] Fix `GITHUB_TOKEN` in `/home/lumen/agent/.env`** — `fleet-search github --code`
  401s and `github_scout` is rate-limited on 1 of 14 topics per run. Rotate/re-add, then
  re-verify with `fleet-search github "x" --code` (expect `ok:true`) and one clean scout run
  with no `rate limited` warning. Minting the token is a Frank credential act *if* the old
  one was revoked; check for a renamed/commented key first (this session could not read the
  file — the guard hook correctly blocks credential reads).
- **[P2] Build `fleet-search hubs`** (R2) — lift `expand_hub` from
  `research/tools/hub_expansion_probe.py`, add hub detection + `--max-hubs` + `via_hub`
  provenance + the return cap from §5.
- **[P2] Add `--queries` + RRF fusion to `fleet-search`** (R4).
- **[P3] Quality-filter stage** (R3) — Tier 1 free filters; Tier 2 Scorecard/ecosyste.ms
  annotation on top-30 only, emitted as numbers not a collapsed score.
- **[P3] Rewire `lumen_scout_graph.py`** (R5) — drop the `awesome-` penalty, invert the star
  sort behind a `--max-stars` ceiling, demote the substring scorer to a gate.
- **[P4] Re-run `research/tools/hub_expansion_probe.py` after R1** — authenticated, to
  confirm the candidate pool and drop set are unchanged by the rate-limit repair.

## 7. Evidence index

| Claim | Evidence |
|---|---|
| Scout queues mega-popular repos; 19 queued, median 36,860★ | `/home/lumen/agent/logs/scout_graph.log`, 2026-09-10 06:01:21 block |
| Scout rate-limited, unauthenticated | same log, 06:00:15 `fetch_repos: rate limited on topic:langchain`; 81 s runtime in `journalctl -u lumen-scout.service` |
| `fleet-search github` unauthenticated / `--code` 401 | live run, §3.4 and §0; ISSUE-LOG row 325 |
| Scout query shape, scorer, skip patterns, threshold | `lumen_scout_graph.py:147,151,183-194,207` and `:57-70` |
| Hub expansion yield (3,882 new repos, 1.73 MB, 0.851 s) | `research/wq1717_hub_expansion_probe.json`; rerun via `python3 research/tools/hub_expansion_probe.py mcp ai-agent llm-tools` |
| Second-domain hub yield (299 papers, 96 repos) | `raw.githubusercontent.com/hemingkx/SpeculativeDecodingPapers/main/README.md`, 124,977 B |
| Citation-sort demotes the seminal paper | two `api.openalex.org` calls, §3.2 |
| Scorecard / ecosyste.ms / OpenAlex reachable and free; S2 429 | live `curl` status + size + time, §3.3 |
| Query reformulation 0 → 6 results | three live `fleet-search github` runs, §3.4 |
| Every literature citation (year, cites, authors) | `api.openalex.org/works?search=<title>`, queried 2026-09-11 |

**Tool gaps hit this session** (neither retryable; recorded so the allowlist can be
widened): `WebSearch` and `WebFetch` both return "Claude requested permissions … but you
haven't granted it yet" — already ISSUE-LOG rows 321 and 323. Every external claim above
was therefore verified through `curl` and `fleet-search` instead, which is the better
evidence anyway: live API status codes rather than a doc page's assertion about them.
