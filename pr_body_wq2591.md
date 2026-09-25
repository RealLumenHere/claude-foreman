## What

wq#2591 — judgment on wq#2587's CONTRADICT of wq#2569 (lumen-bin PR#131). Adds
`2591_judgment_wq2569_fleet_search_github_rate_limit.md`. Docs only; no code in this repo
changes.

**Verdict: wq#2569 is NOT reopened. Corrected fast-follow filed as wq#2598 (pri 1,
`deps=2569`, `needs repo(/home/lumen/bin)`, sonnet-5@high). PR#131 should merge as-is
rather than be held for it.**

## Why

The defect is real and was reproduced here independently before deciding, against a copy of
`fleet-search` at `035778c` under `/tmp/fworker-wq2591/probe/` (no writes to
`/home/lumen/bin`): `cmd_github`'s guard fires on `X-RateLimit-Remaining: 0` regardless of
the response body, so the last request a GitHub budget still permits — which succeeds and
carries real items — comes back `ok:false` "rate limited" with the results dropped.

Three things drive the disposition:

1. **A reopen re-dispatches the sentence that caused it.** wq#2569's AC(2) lists
   `X-RateLimit-Remaining=0` as an unqualified trigger, and `wq claim` prints only
   `detail` (`/home/lumen/bin/wq:1752`, `:1809`) — `wq release`, the only reopen mechanic,
   would hand attempt 2 the same text with the correction invisible.
2. **PR#131 is a net improvement even with the defect.** `main` (`a562378`) has no guard at
   all and searches unauthenticated, so exhaustion there returns a silent empty `ok:true`
   (wq#2564). Holding the merge keeps the silent failure live longer than shipping the
   honest one.
3. **A fast-follow cannot branch from `main` yet.** `035778c` is not an ancestor of `main`,
   so wq#2598 carries `deps=2569` plus a checkable base precondition: if
   `grep -c X-RateLimit-Remaining /home/lumen/bin/fleet-search` is 0, block instead of
   re-implementing wq#2569.

## How

Corrected spec handed to wq#2598: gate the header trigger on
`not (data or {}).get("items")` (on `items`, not `total_count`, since `out` is built from
`items`), keep 403/429 unconditional, surface the exhausted budget as
`rate_limit_remaining`/`rate_limit_reset` fields on the `ok:true` envelope rather than as a
failure, and test both a populated and an empty body against the same header — the
single-body test gap is what let this pass its own 10/10 gate.

wq#2569 annotated via `wq note` (left `done`, PR#131 left open and unedited); ISSUE-LOG
#714 appended.

## Testing

`/tmp/fworker-wq2591/probe/tests/test_wq2591_judgment_probe.py` — 4 passed, verify-only,
never committed to lumen-bin. Case 1 reproduces the discard
(`(200, {items:[one real repo], total_count:1}, None, {remaining:"0"})` → exit 2, `ok:false`,
no `results` key); cases 2-3 confirm the empty-items and 403 behaviours the fast-follow must
preserve; case 4 evaluates the proposed condition over all seven header/body combinations.
wq#2569's shipped 10-test suite was re-run against `035778c` and is green — the gap is
coverage, not a failing test.

## Risk / rollback

Documentation and queue bookkeeping only. Rollback is `git revert` of this commit plus
retiring wq#2598; nothing in the runtime path is touched.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
