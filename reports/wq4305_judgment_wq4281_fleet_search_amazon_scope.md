# wq#4305 — judgment on wq#4281 (fleet-search Amazon SubscriptionId strip)

**Verdict: UPHOLD wq#4299's CONTRADICT. Option (a) — widen the scope, but fix it
centrally at the two sinks, not per backend. The row-1437 recurrence risk stays
OPEN; it is not closed by a3fef0e.**

Independently re-derived here (not taken from wq#4299's report): the scope gap is
real, and a second, more severe fact turned up that the verify-pass under-weighted
— the `fleet-search` copy agents actually execute has *none* of the fix.

## 1. The fix is not live on the executing copy (new, and the bigger problem)

Doctrine invokes `/home/lumen/bin/fleet-search`. That working tree is checked out on
`feat/wq4281-strip-amazon-subscriptionid`, which is **2 commits behind `main`** and
does not contain the fix:

- `git -C /home/lumen/bin rev-list --left-right --count main...HEAD` → `2  0`
- `git -C /home/lumen/bin merge-base --is-ancestor a3fef0e HEAD` → non-zero (fix
  absent from the checked-out branch)
- `grep -n "_clean_amazon\|AMAZON_NOISE_PARAMS" /home/lumen/bin/fleet-search` → **0
  matches** (the ticket's own liveness line claims 6; that claim describes `main`'s
  blob, not the live file)

So today every `fleet-search` subcommand — including `web` — passes the
`SubscriptionId` param through verbatim. The mask-tripwire false-burn of
wq#4267/wq#4268 can refire from the path wq#4281 was written to close.

This is a recurrence of the wq#900 / wq#1313 class and the direct consequence of
ISSUE-LOG row 1441 (a non-`--worktree` `lumen-pr start` parked the shared primary
tree on a feature branch, and `ff_primary_after_land` then refuses to resync it).
Row 1441 logged the parking; nobody logged that it also de-deployed the fix.

Tree state for whoever resyncs it: 8 modified tracked files, plus untracked
`aider-fworker` which `f28c22f` wants to track (both copies 64 lines, so a plain
`git checkout main` may refuse on the untracked-overwrite check). Not touched from
here — moving HEAD in a shared primary tree other sessions may be running is what
row 1441 already cost once.

## 2. The scope gap on `main`'s blob (wq#4299's finding, re-confirmed)

`main:fleet-search` wires the cleaners into 3 of the result-producing paths. The
uncleaned ones, read at the live file's line numbers — **rev-pinned to `5d289ea`,
the lumen-bin primary's HEAD as of this judgment**, so the citations stay re-runnable
after wq#4308 shifts the live offsets by a3fef0e's +32 lines:

| surface | builds result from | file:line |
|---|---|---|
| `cmd_hn` | raw `comment_text`/`story_text`/`url` | `fleet-search:616-620` |
| `cmd_so` | raw `body` (tags stripped only), `link` | `fleet-search:640-643` |
| `cmd_github` code/issues/repos | raw `text_matches`, `body`, `description` | `fleet-search:581-597` |
| `cmd_reddit --deep` | `_fetch_and_extract` → `comments`/`content`, assigned **after** searxng's clean pass | `fleet-search:456-464` |
| `cmd_read` | raw extracted page `content`/`comments` | `fleet-search:899-906` |

`cmd_read` is the one wq#4299 listed but did not weigh: it is the highest-volume
Amazon-text surface in the tool, since reading any page that links products emits
and caches the link bodies verbatim.

Two further holes a per-backend fix structurally cannot close:

- **Titles are never cleaned**, even on the 3 wired backends — only `url` and
  `snippet` are (`searxng_search`, `brave_api_search`, `deepseek_search` in
  a3fef0e's diff all keep `r.get("title","")` raw).
- **Cache replay bypasses every backend.** `cmd_reddit:446`, `cmd_read:894` and
  `cmd_web`'s cached branch emit `hit["results"]` straight from disk. Records
  written before the fix (or by the currently-live uncleaned binary) replay
  unscrubbed for the full 14-day TTL no matter how many backends get wired.

## 3. Why (a), and why centrally

wq#4281's own detail text sets the broad goal — *"every future Amazon hit will
refire with a new hash … drop the SubscriptionId (and associate tag noise) query
params from result urls/snippets **before emit**"* — and the landed code's own
comment repeats it (*"before a result/snippet reaches cache, prior-art, or emit"*).
The one-sentence acceptance clause is narrower than the ticket it belongs to, so
option (b) would close a stated recurrence as solved on the strength of a clause
that was only ever a test recipe. Hacker News and StackOverflow routinely carry
Amazon affiliate links in comments and answers; this is a live path.

Per-backend wiring is also the wrong shape. It is O(backends) edits that already
missed 5 of 8 surfaces on first pass, it re-misses on every new subcommand, and it
cannot reach the cache-replay or title paths at all. Every result-producing path
instead funnels through exactly two sinks — `cache_store()` (`fleet-search:227`,
which also feeds `_append_prior_art`, `:246`) and `emit()` (`fleet-search:117`) —
11 and 29 call sites respectively, all of them downstream of every surface in the
table above, and both of them downstream of cache replay. One recursive scrub at
those two functions covers all 8 surfaces, future subcommands, and replayed
records, and is strictly where the tripwire risk lives: the tripwire reads
fleet-search's stdout and its on-disk cache, never its intermediate variables.

Recommended shape for the follow-up: keep `AMAZON_NOISE_PARAMS` /
`_clean_amazon_url` / `_clean_amazon_noise` as-is, add a `_scrub(obj)` that walks
dicts/lists/strs applying `_clean_amazon_noise` to every string value, call it once
at the top of `cache_store()` and once in `emit()`, and *remove* the 6 per-backend
call sites so there is one owner. Keep the three backends' behaviour covered by
wq#4281's existing 4 tests (they must still pass through the central path).

## 4. Prior art considered

`fleet-search web "strip affiliate tracking query parameters from urls library
python clearurls"` surfaces Unalix (dependency-free Python implementation of the
ClearURLs ruleset), `url-sanitize`, `detrack`, `url-strip`. **Rejected as a
dependency:** `fleet-search` is stdlib-only by design, and the requirement here is
not general detracking but suppressing one tripwire false-positive class, which a
7-entry param set already covers. Worth mining ClearURLs' Amazon rule block once to
extend `AMAZON_NOISE_PARAMS` (e.g. `pd_rd_*`, `psc`, `ref_`, `_encoding`); noted on
the follow-up ticket rather than guessed at here.

## 5. Disposition

- wq#4281 stays `done` — its literal acceptance passed, 4/4, and that finding is
  not disturbed. Not reopened; the widened scope lands as its own tracked ticket so
  the audit trail keeps both ACs distinct.
- **wq#4308** filed (p1, build): bring the lumen-bin primary tree up to its default
  branch via `sync-live` so a3fef0e is actually live. This outranks the scope
  widening — right now the narrow fix isn't deployed either.
- **wq#4309** filed (p2, build, `deps=4308`): central `_scrub` at
  `cache_store`/`emit` per §3, with acceptance covering `hn`, `so`, `github`,
  `reddit --deep`, `read`, titles, and a replayed cache record.
- ISSUE-LOG **row 1446** appended for the de-deployed-fix finding (§1), carrying the
  doctrine lesson: a verify-pass that reads a blob from an isolated checkout must
  also assert the fix is an ancestor of the *live* tree's HEAD, or it verifies code
  nothing runs. **Row 1447** appended for a `guard.sh` false positive hit on the way
  here — it matched a git verb quoted inside a `wq add --detail` string and refused
  the ticket-filing call; reworded prose, no retry.
- Fix not re-implemented here (archetype 8), and `fleet-search` lives in
  `/home/lumen/bin`, a different repo from this worktree.
