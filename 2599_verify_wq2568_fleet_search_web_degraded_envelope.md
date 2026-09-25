# wq#2599 — verify of wq#2568 (lumen-bin PR#134, merged c7c0dde)

Verifier: claude-sonnet-5@high, fresh instance (same model as author). Verdict:
**CONTRADICT** (one blocking finding; everything else confirmed). Escalated per
archetype 8 to opus-5@high — **wq#2601** filed, `deps=2568`. Full ISSUE-LOG entry: #715.

Note on repo location: wq#2568's deliverable is `fleet-search` in `lumen-bin`
(`/home/lumen/bin`), not this (`foreman`) repo — the dispatcher checked this ticket
out as a foreman worktree per the cross-repo dispatch quirk logged as ISSUE-LOG #708.
`wq2568_cross_repo_evidence.md` (committed on `main` at `0cf00e8`) names the real
change: lumen-bin PR#134, merged (squash) as `c7c0dde` on `main`. Verified directly
against the live, already-merged `/home/lumen/bin/fleet-search` (an additional
working directory of this session) — no nested worktree needed since the PR was
already merged before this verify pass claimed.

## Per claim

| wq#2568 claim | Result | Evidence |
|---|---|---|
| AC1: `ok:false`/`degraded` + engine list when every general engine is unresponsive | CONFIRMED (live path) | `fleet-search --no-cache web "systemd StartLimitBurst"` → `{"source":"searxng","ok":false,"unresponsive_engines":[["brave","too many requests"],["duckduckgo","CAPTCHA"],["google cse","too many requests"],["startpage","Suspended: CAPTCHA"]],"error":"all general web-search engines suspended/blocked (brave, duckduckgo, google cse, startpage); fallback failed: no BRAVE_API_KEY configured (see wq#2595...)"}`, exit code 2 (confirmed via `> file; echo $?`, not through a pipe — a pipe's `$?` reflects the pipe's last stage, not `fleet-search`'s). Matches the cross-repo evidence note's own live-verified quote exactly. |
| AC2: quota design — per-engine budget/backoff, cache TTL, ≥1 keyed engine | PARTIALLY CONFIRMED, see contradiction below | `brave_api_search()` (fleet-search:211-234) is real, gated on `BRAVE_API_KEY` via the existing `_env_token()` broker helper, inert until wq#2595 (filed, Frank-gated) mints a key — sound design, correctly inert-not-crashing when unset (`test_all_general_engines_suspended_without_key_returns_ok_false`). Per-engine budget/backoff is correctly *not* duplicated (SearXNG's own `unresponsive_engines` already carries that signal) — reasonable per the evidence note's own reasoning. The **cache TTL** half of this AC is where the contradiction lives (below). |
| AC3: test covering the all-suspended envelope | CONFIRMED | `tests/test_wq2568_web_degraded_envelope.py`, 5/5 independently re-run (`python3 -m pytest`) — all-suspended-no-key, all-suspended-with-key-fallback, all-suspended-key-itself-429, partial-unresponsive-still-ok, clean-success. Regression: `test_reddit_comment_tree.py` + `test_fetch_ladder_challenge.py`, 33/33 independently re-run. |
| Control-flow sanity (not an explicit AC, but the diff's shape invites the question) | CONFIRMED sound | `cmd_web`'s `if fallback is not None: ...emit(success)... ; emit(failure)` (fleet-search:249-258) has no visible `return` between the two `emit()` calls, which looks like a double-emit bug on first read. It is not one: `emit()` (fleet-search:80-82) unconditionally `sys.exit()`s after printing, so the second `emit()` call is unreachable whenever the first one fires. Confirmed via `test_all_general_engines_suspended_with_brave_key_falls_back_to_ok_true`, which asserts a single well-formed `ok:true` envelope with `SystemExit` from the fallback branch alone. |

## Contradiction: pre-fix cache entries still return the exact bug wq#2568 exists to kill

`cmd_web` (fleet-search:236-261) checks the on-disk query cache (`cache_lookup`,
fleet-search:238-242) *before* calling `searxng_search` at all, and serves any hit
within `CACHE_TTL_DEFAULT` (14 days for the `web` kind, fleet-search:33) without
inspecting what wrote that record. The fix only changes what happens on a fresh
SearXNG round-trip; it does nothing about records already sitting in
`research_cache/*/search_cache.jsonl` that the *old*, pre-fix `cmd_web` wrote during
the outage — which cached exactly `{ok:true, results:[]}` for any query, with no
`unresponsive_engines` field at all (that field didn't exist in the pre-fix envelope).

Reproduced live, not hypothetical, using the ticket's own canonical demonstration
query with the default (cache-on) invocation — the common case, since `--no-cache`
is the opt-out, not the default, and is exactly the flag the evidence note's own
"live-verified" quote used to demonstrate the fix (sidestepping the cache entirely):

```
$ /home/lumen/bin/fleet-search web "systemd StartLimitBurst" > out.json; echo $?
0
$ cat out.json
{
  "source": "searxng",
  "ok": true,
  "query": "systemd StartLimitBurst",
  "cached": true,
  "cached_at": "2026-09-25T04:06:07Z",
  "results": []
}
```

`cached_at` (04:06:07Z) is ~48 minutes before the fix commit landed (04:54:08Z) —
i.e. this record was written by the pre-fix code during the real wq#2564 outage, and
is still being served, exit 0, `ok:true`, empty results, with zero indication a
search never really happened. This is not a new or different bug; it is the *original*
wq#2564/wq#2568 symptom, unmodified, now hiding behind a cache hit instead of a live
SearXNG round-trip. Any query run during the outage window and re-run (through the
normal cached path) within the next up-to-14-days will reproduce it — including,
notably, the mandatory prior-art `fleet-search web` step (CLAUDE.md doctrine) for
any topic an agent happened to search for during the outage.

wq#2568's own AC(2) names "result cache TTL" as (part of) the quota-design mechanism
satisfying that clause. This isn't an unrelated system interacting badly with the fix
— the cache is the very mechanism the AC credits, and it's the reason the fix's own
canonical worked example still fails end-to-end through the default code path.

Scope: narrow but real. `cache_store` is only reached post-fix on a genuine
success/fallback-success path (`fleet-search:250`, `:259` — the `ok:false` branches
return via `emit()`/`sys.exit()` before reaching it), so **no new bad entries can be
written going forward** — this is entirely a backlog problem bounded to records
written before `c7c0dde` landed, decaying naturally over 14 days.

## Attempts to break the fix that did NOT find a defect

- Only two call sites of `searxng_search` exist (`cmd_web`, `cmd_reddit`), both
  updated consistently to the new 3-tuple return; `cmd_reddit` unpacks
  `unresponsive` but never uses the all-suspended-fallback logic — correct, since
  that logic is scoped to `WEB_SEARCH_ENGINES` (general web crawlers), not reddit
  discovery, and wq#2568's AC never asked for reddit coverage.
- Engine-name matching (`WEB_SEARCH_ENGINES = {"brave", "duckduckgo", "google cse",
  "startpage"}` against SearXNG's `unresponsive_engines` names) is exact-string, not
  fuzzy — checked against the real live outage's `unresponsive_engines` payload
  during this verify pass, which used precisely those four strings.
- `down = {name for name, _reason in unresponsive}` assumes each entry unpacks to
  exactly 2 elements; the live payload observed during this verify pass (see
  contradiction repro above, pre-fix cached record aside) and the evidence note's
  own quoted live-verified output both confirm SearXNG's real shape is `[name,
  reason]` pairs — not a hypothetical assumption.
- `brave_api_search`'s 401/429/generic-status handling and its `[:n]`/`min(max(n,1),
  20)` count clamp are all correct on inspection; inert (no key) path confirmed not
  to crash.

## Actions taken

| Action | Rationale |
|---|---|
| ISSUE-LOG **#715** appended | Records the stale-cache false-positive and its scope, per doctrine |
| **wq#2601** filed, pri 1, `deps=2568`, `claude-opus-5@high` | Judgment ticket per archetype-8 protocol: decide a one-time cache-purge sweep vs. hardening `cache_lookup` to refuse empty-result-no-metadata hits vs. accepting the bounded 14-day exposure window |
| wq#2568 left `done`, lumen-bin PR#134/`c7c0dde` left as-is, not reopened or edited here | Verify-only per archetype 8 — do not re-implement; opus-5@high judgment decides disposition |
| Live `wq2568_cross_repo_evidence.md`'s claims (AC1 code path, tests) all independently re-run and confirmed | No re-implementation; probe only, plus one fresh live invocation revealing the cache gap |
