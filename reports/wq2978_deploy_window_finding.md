# wq#2978 — Commander-drop attribution: deploy confirmed, 48h data window not yet open

**Finding: too early to attribute, not blocked.** wq#2925 landed the observability deploy
this ticket depends on at 2026-09-29 19:02:00 UTC (worker commit `171ab1e`). This ticket's
own instructions are to query Workers Logs "after 48h" so the /register, /authorize-complete,
/token, and /mcp counts have enough real traffic to attribute drops by path rather than by
noise. At investigation time (2026-09-29 19:0x UTC) the deploy was minutes old, so no such
window exists yet — querying now would not produce a founded finding, so this pass stops at
confirming the pipe is live and files the actual attribution work as a follow-up ticket dated
past the window.

## Confirmed: the observability pipe the deploy was supposed to enable is actually live

`git -C /home/lumen/projects/mcp-oauth-worker merge-base --is-ancestor 9dedaee 171ab1e` exits
0 — commit `9dedaee` (wq#2925 follow-up, "enable Workers Logs (observability) so auth/drop
failures leave a trace") is an ancestor of `171ab1e`, the commit wq#2925's own `done` notes
record as deployed via `wrangler deploy`. The diff itself
(`mcp-oauth-worker/wrangler.toml`, added by `9dedaee`):

```toml
[observability]
enabled = true
head_sampling_rate = 1
```

is config-only (no code change, per `9dedaee`'s own commit message), takes effect on next
deploy, and that deploy (`171ab1e`) has now happened. So Workers Logs is recording path +
status per request as of 2026-09-29 19:02:00 UTC — the prerequisite this ticket was blocked
on (its own "GAP: worker had no observability" note) is resolved.

## Why no query was run this pass

The ticket's acceptance test for a useful query is elapsed time, not code: "after 48h query
Workers Logs ... count /register vs /authorize-complete vs /token 200; correlate any /mcp 401
invalid_token with the cloudflared/DC restart timeline." Three minutes of post-deploy traffic
cannot support that count or that correlation — a query run now would either come back empty
or, worse, get treated as a real sample size when it is not one. Per doctrine ("never guess a
root cause... verify it"), the honest move is to wait for the window rather than publish a
premature read.

## Follow-up

wq#3158 carries the actual post-window query: status>=400 by path, /register vs
/authorize-complete vs /token 200 counts, and correlation of any /mcp 401 invalid_token
against the cloudflared/DC restart timeline — not before 2026-10-01 19:02 UTC (48h after the
`171ab1e` deploy). It deps on wq#2925 (met) and its own detail carries the do-not-dispatch
date; a worker that picks it up early should re-verify elapsed time live and release, the same
way this pass did.

## Reproduction

- `git -C /home/lumen/projects/mcp-oauth-worker log --oneline -1 9dedaee` →
  `9dedaee mcp-oauth-worker: enable Workers Logs (observability) so auth/drop failures leave a trace (wq#2925)`
- `git -C /home/lumen/projects/mcp-oauth-worker merge-base --is-ancestor 9dedaee 171ab1e && echo ancestor` → `ancestor`
- `git -C /home/lumen/projects/mcp-oauth-worker show 9dedaee -- mcp-oauth-worker/wrangler.toml` → shows the `[observability]` block added
- wq#2925 `done` notes: deploy commit `171ab1e`, `done_at 2026-09-29 19:02:00`
