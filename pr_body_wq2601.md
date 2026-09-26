## What

Adds `reports/2601_judgment_wq2568_fleet_search_web_cache_staleness.md`: the judgment on wq#2599's
CONTRADICTED verify-pass verdict against wq#2568 (lumen-bin PR#134). No code changes — the
fix itself is dispatched as wq#2603 against `/home/lumen/bin`.

## Why

wq#2599's finding is upheld, and was re-reproduced here rather than taken on report:
`fleet-search web "systemd StartLimitBurst"` still returns
`{ok:true, cached:true, cached_at:2026-09-25T04:06:07Z, results:[]}` exit 0 from a cache
record written during the outage, ~48 min before the fix landed. `--no-cache` on a fresh
query confirms all four general engines are still suspended with no `BRAVE_API_KEY`
(wq#2595), so the 14-day exposure window is not draining against a healed system.

That is the exact silent false positive wq#2568 was filed to eliminate, now hidden behind a
cache hit, on the queries an agent already ran during the outage — the ones a redispatched
worker is most likely to re-run.

## How

**wq#2568 is not reopened.** Its AC(1) is written about the live envelope and PR#134
satisfies it; the hole is one layer down in the cache, which the ticket's `detail` never
specified — and `wq claim` prints only `detail` (`wq:1752`/`:1809`), so a reopen would
re-dispatch the same silent spec.

**The remedy is the read-side guard, not a purge sweep.** `search_cache.jsonl` is
append-only and written concurrently by every agent with no locking, so a sweep is a racy
rewrite; it is one-time while `cache_store` (`fleet-search:259`) still stores empty results
on a *partial* outage, where `WEB_SEARCH_ENGINES <= down` (`:247`) is false; and the affected
set is sha256-keyed (`:118`) across a global cache plus one `research/` dir per project, so
sweep completeness is unverifiable. The guard writes nothing, makes the backlog inert, and is
testable offline — which matters while the engines are down.

Filed **wq#2603** (pri 1, `deps=2568`, `needs repo(/home/lumen/bin)`): `cache_lookup` treats
an empty-`results` record as a miss while keeping "freshest *valid* wins"; `cache_store`
refuses to append one. That extends an invariant the codebase already holds at
`fleet-search:152`, where `PRIOR_ART.md` already declines to record empty results.

## Testing

Docs-only; nothing executable changed. Evidence for the claims in the doc is the two live
runs above (cached `ok:true`/`results:[]` on the canonical query; `ok:false` exit 2 with the
full `unresponsive_engines` list under `--no-cache`), plus the cited `fleet-search` and `wq`
line numbers, each re-grepped this pass.

## Risk / rollback

None to running systems — one new markdown file plus this PR body. The accepted cost lives
in wq#2603, and is stated there and in the doc: a genuinely zero-result query is re-searched
on every invocation instead of served from cache, giving up that much of wq#2568 AC(2)'s
"result cache TTL" quota mechanism. Rollback is `git revert`.

ISSUE-LOG row 717 resolves row 715.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
