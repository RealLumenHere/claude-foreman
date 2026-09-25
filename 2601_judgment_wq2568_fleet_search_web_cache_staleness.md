# wq#2601 — judgment on wq#2599's CONTRADICTED verdict against wq#2568 (lumen-bin PR#134)

Judge: claude-opus-5@high, fresh instance, worktree `wq2601-a1`.
Both load-bearing facts were re-observed here, against the live `/home/lumen/bin/fleet-search`
(post-merge `c7c0dde`), not taken from wq#2599's report:

- **The defect reproduces.** `fleet-search web "systemd StartLimitBurst"` (default cache-on
  path, the ticket's own canonical query) returned
  `{"source":"searxng","ok":true,"query":"systemd StartLimitBurst","cached":true,"cached_at":"2026-09-25T04:06:07Z","results":[]}`
  — exit 0, from a record written ~48 min before the fix commit landed (04:54:08Z).
- **The outage is still live.** `fleet-search --no-cache web "..."` returned `ok:false`, exit 2,
  `unresponsive_engines=[brave "too many requests", duckduckgo CAPTCHA, google cse "too many
  requests", startpage "Suspended: CAPTCHA"]`, `fallback failed: no BRAVE_API_KEY configured
  (see wq#2595)`. The 14-day exposure window is not draining against a healed system.

The doctrine-mandated prior-art search could not run this pass: the search backend is the
subject of the outage (second command above). Judgment therefore rests on the code and the two
live observations.

## Verdict

**wq#2599 is upheld: this is a real defect, reproducing on the ticket's own worked example.
wq#2568 is NOT reopened. The remedy is the read-side cache guard, not the purge sweep, and it
lands as a pri-1 fast-follow (wq#2603) against `/home/lumen/bin`.**

### Why not reopen wq#2568

wq#2568's AC(1) is written about the live envelope ("when `unresponsive_engines` covers every
general engine"), and PR#134 satisfies it — wq#2599 confirmed the live path, the shipped test
file (5/5) and both regression suites (33/33) independently, and I re-confirmed the live
envelope above. AC(2) names "result cache TTL" only as one of three quota mechanisms; nothing
in the detail text says anything about serving a stale hit. A reopen would hand attempt 2 that
same silent detail: `wq claim` prints `detail` and never `notes` (`/home/lumen/bin/wq:1752`,
`:1809` — established in wq#2591), so the correction would be invisible and the worker would
re-deliver the live-path fix that already exists.

### Why the read-side guard, not the purge

The purge (rewrite `research_cache/*/search_cache.jsonl`, dropping empty-result web records
older than `c7c0dde`) loses on all three axes:

1. **It is a racy write to an append-only file.** `search_cache.jsonl` is appended by every
   fleet agent concurrently with no locking (`cache_store`, `fleet-search:141-158`, plain
   `open(path,"a")`). Purging means rewrite-in-place, which can silently drop lines appended
   mid-sweep. The guard writes nothing at all.
2. **It is one-time; the hole stays open.** `cache_store` at `fleet-search:259` is still
   reached with an empty list whenever searxng answers with *some* engine up and zero results —
   `WEB_SEARCH_ENGINES <= down` is false, so no `ok:false` branch fires. The next partial
   outage refills the backlog. wq#2599's "no NEW bad entries can be written going forward"
   holds only for a *total* outage.
3. **Its blast radius cannot be measured, so its completion cannot be verified.** Records are
   keyed by `sha256(kind|query|n)[:16]` (`fleet-search:118`) and live in a global
   `/home/lumen/agent/research_cache` plus a `research/` dir per project. Enumerating the
   affected set means reading every project dir; confirming a sweep caught all of them is
   strictly harder than confirming a two-branch guard. (This worker could not read those paths
   at all — outside its `--add-dir` bounds — which is itself evidence that the sweep belongs in
   neither this lane nor a general worker's.)

The guard makes the backlog inert without touching it, and is testable **offline** — no working
engine needed, which matters while the outage is unresolved.

### Why not accept the window (option C)

The exposed set is precisely the queries already run during the outage — i.e. the queries a
redispatched worker on the *same* ticket is most likely to re-run, which is how row 667
(wq#2469) went undiagnosed in the first place. A cached `ok:true / results:[]` tells the
mandatory prior-art step "nothing exists" with `cached:true` as its only tell. That is the
exact false positive wq#2568 was filed to eliminate; accepting it for 14 days re-accepts the
original bug for the population most likely to hit it.

## Spec for wq#2603

Precedent to extend, not invent: `cache_store` already refuses to record an empty result set as
prior art — `if project and results and isinstance(results, list)` (`fleet-search:152`) skips
the `PRIOR_ART.md` append on empty. The codebase already holds "empty is not a finding" one
layer up; this pushes the same invariant into the JSONL cache.

- **`cache_lookup` (`fleet-search:121-138`):** while scanning, skip any record whose `results`
  is an empty list — it is not a hit. Keep "last (freshest) *valid* wins": an older non-empty
  record for the same key, still within TTL, is genuine prior art and should still be served in
  preference to a miss. Dict payloads (`cmd_read`, `fleet-search:677-688`) must be unaffected.
- **`cache_store` (`fleet-search:141-158`):** do not append a record whose `results` is an empty
  list. Stops the backlog from regrowing on a partial outage.
- **Tests (offline, no network):** (a) cache file holding only an outage-era empty-result web
  record for key K → `cache_lookup` returns `None`; (b) older non-empty record for K plus a
  newer empty one → returns the non-empty record; (c) `cache_store(..., [])` appends no line;
  (d) non-empty store→lookup round-trip unchanged; (e) `read`'s dict payload still caches and
  serves. (a) and (c) must fail against `c7c0dde` before the change.
- **Not in scope:** any sweep or edit of existing `search_cache.jsonl` files. The guard makes
  them inert; deleting them buys nothing and risks a concurrent write.

**Cost, stated plainly:** a query that genuinely has zero web results is re-searched on every
invocation instead of being served from cache, and that much of AC(2)'s "result cache TTL"
quota mechanism is given up. An empty result set conserves no meaningful quota, and the
alternative is caching the claim "no prior art exists" for 14 days — the failure wq#2568
exists to prevent. Take the cost.

## Actions taken

| Action | Rationale |
|---|---|
| wq#2568 left `done`, PR#134 left merged, annotated via `wq note` | Live-path deliverable is confirmed correct twice over; a reopen re-dispatches a `detail` that never mentioned the cache |
| **wq#2603** filed, pri 1, `deps=2568`, `needs repo(/home/lumen/bin)`, sonnet-5@high | Spec above; `needs` tag set so the worker gets a lumen-bin worktree rather than repeating ISSUE-LOG #708's cross-repo quicksand |
| ISSUE-LOG row 715 resolved with a pointer to this file | Row 715 recorded the contradiction; the judgment closes it |
