# wq#993 — fleet-search residual gaps (design + close)

_2026-08-27 · session wq993-search-84cb41_

## Situation
wq#993 ("Build Reddit/GitHub/forum search tooling") was largely delivered by its own
child ticket **wq#999** (SearXNG live on 127.0.0.1:8888 + `/home/lumen/bin/fleet-search`
router + per-project cache + harness wiring + research gate wq#991/#1006). The dispatch
prompt that launched this session predates wq#999 and asked to build what already exists.
Per REVERSE-STALE-WORK: not rebuilt. Verified the parent's acceptance test instead.

## Acceptance test (ticket's own): `miko 3 robot unlock`
- Reddit **comments** come back: PASS — `reddit`→`read` on the r/hacking Freeko thread
  extracted **24 comments** (author + depth + body), not titles.
- GitHub **issues + repos** come back: PASS — both paths return live data.

## Residual gaps vs ticket spec ("repos, issues, discussions, and code")
Root cause: wq#999 shipped the GitHub surface as repos+issues only, and `reddit` as
discovery-only. Class of defect = **GitHub search surface incomplete + reddit command
does not itself parse comments**. Evidence:
1. **code search absent** — highest impact: `github "miko"` (repos) buried the real robot
   repos (openmiko/freemiko) under unrelated high-star "miko" projects. Code search
   (verified live, 243 hits) surfaces `openmiko/openmiko → openmiko-gen.sh,
   upgrade_openmiko.sh` directly.
2. **discussions absent** — REST has no discussions search; GraphQL `search(type:DISCUSSION)`
   verified live (http 200, clean).
3. **`reddit` parses titles only** — ticket item 1 says "parse comments, not just titles".

## Approach (additive, no rebuild; fix the class)
- Extract one reusable `_fetch_and_extract(url)` from `cmd_read` (single extraction path;
  old.reddit rewrite + trafilatura + bs4 comment tree). `cmd_read` and the new reddit
  `--deep` both call it — no duplicated crawl logic.
- `cmd_github`: add mutually-exclusive `--code` (REST /search/code, text-match Accept) and
  `--discussions` (GraphQL) alongside existing default-repos / `--issues`. Same envelope.
- `cmd_reddit --deep [--threads N] [--depth D]`: after SearXNG discovery, auto-`read` the
  top N reddit threads and attach their `comments` inline, so the reddit command alone
  returns comment content.
- Envelope contract unchanged (source/ok/results/error, exit 0/2, fail-soft). Worker
  allow-list already grants `Bash(/home/lumen/bin/fleet-search *)` — new flags need no
  allow-list change.

## Rollback
`cp /home/lumen/bin/fleet-search.bak.20260827_140214_pre_wq993_residuals /home/lumen/bin/fleet-search`

## Test-to-failure plan
New: `github --code openmiko` (expect openmiko scripts), `github --discussions "miko robot"`
(expect ok, count 0 clean), `reddit --deep "miko 3 robot unlock"` (top thread + comments).
Regression: `github` repos, `github --issues`, `web`, `reddit` (no deep), `read`, `hn`, `so`
all still emit valid envelopes with correct exit codes.
