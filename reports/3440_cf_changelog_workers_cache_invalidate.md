# wq#3440 triage: CF changelog "Workers Cache — mark cached responses stale with invalidate()"

Source: https://developers.cloudflare.com/changelog/post/2026-09-29-workers-cache-invalidate/
(2026-09-29), filed by cf-changelog-watch (wq#3335). Read via `fleet-search read` on 2026-10-04.

## Verdict: informational, no action required. Close.

None of our Workers use the Workers Cache. A grep for `caches.default`, `caches.open`,
`cache.put(`, `purge_cache` and `.purge(` over `*.ts/*.js/*.mjs/*.py/*.toml` in mailbot,
foreman, agent, pokemon and this repo (node_modules excluded, 2026-10-04) found no matches.
The mailbot Worker and mail-probe handle email and D1 and serve nothing cacheable, so neither
`purge()` nor `invalidate()` has a caller. ISSUE-LOG.md has no Workers Cache entries.

## What changed (summary)

- `invalidate()` is the soft version of `purge()`. `purge()` deletes matching entries, so the
  next request misses. `invalidate()` keeps them and marks them stale, and the cache
  revalidates them with the Worker.
- To revalidate, the cache sends a conditional request built from the stored validators,
  e.g. `If-None-Match` with the cached `ETag`. A `304` keeps the stored body. A `200`
  replaces it.
- It takes the same options as `purge()` (`tags`, `pathPrefixes`, `purgeEverything`), uses the
  same per-entrypoint scoping, and returns the same result object. Call it as
  `ctx.cache.invalidate()`, or import `cache` from `cloudflare:workers`.
- Prerequisite: the Worker emits `ETag` or `Last-Modified` and answers matching conditional
  requests with `304`. An unchanged entry then costs a validator check, not a regeneration.

## Possibly useful later (nothing is filed from this ticket)

If we ever put a cached Worker in front of a public read endpoint (e.g. LPP state at
`pokemon.lumennode.uk`, today a local service behind the tunnel), use `invalidate()` by
tag on state change rather than `purge()`. That avoids a full-miss stampede on the origin.
