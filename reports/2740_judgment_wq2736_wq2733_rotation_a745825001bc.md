# wq#2740 — judgment: wq#2736's contradiction of wq#2733 (rotation row `sha256:a745825001bc`, cloudflare)

**wq#2736 is UPHELD on all three points, and its one hedge is removable.** The row closes
no-rotation — not "probably", and not for the reason either predecessor gave. wq#2733's
evidence is false, wq#2736's replacement evidence is true but unreproducible, and the real
proof is a hash preimage neither of them computed.

Decisions, in the order the ticket asks them:

| # | question | ruling |
| --- | --- | --- |
| 1 | close `a745825001bc` no-rotation? | **Yes — proven, not inferred.** The value *is* the word `-prefixed`; there is no credential to rotate |
| 2 | correct `kb/ISSUE-LOG.md` row 824? | **Yes.** Appended rows 835/836; row 824's status cell now points at 835. Landing it exposed a second gap — see §5 |
| 3 | make `account()` require the prefix adjacent? | **Yes, below a floor** — `harness/mask_replay.py:428`, two tests. A blanket requirement would re-page the split-literal fixtures |

## (1) The flagged value is an English word. Nothing can be rotated.

```
$ python3 -c "import hashlib; print(hashlib.sha256(b'-prefixed').hexdigest()[:12])"
a745825001bc
```

The preimage is exhibited, so this is not an argument about likelihood: the string the
tripwire hashed is the nine-character ASCII fragment `-prefixed`, matching the row's hash
exactly. A Cloudflare API token is its prefix plus 40 characters of `[A-Za-z0-9_-]`; a
dictionary fragment cannot be one. There is nothing to rotate, nothing to confirm 401 on,
and therefore **no ledger entry** — `record_rotated` takes only *confirmed* rotations
(`harness/mask_replay.py:539-545`), and writing a benign value into it would corrupt the one
place that means "this credential is dead".

This is the same class as ISSUE-LOG rows 799/802/804/812/821/824: the masking vocabulary
itself reads as credential-shaped. What makes this row different is that it is the first one
closed on evidence that cannot drift — every other citation in the chain can.

## (2) Both predecessors cited things that do not hold, or do not hold still

wq#2733's citation is simply false, as wq#2736 found and this pass re-confirmed:

```
$ grep -c cfut 2322_verify_wq2288_kv_secret_paren_regression.md
0
$ sed -n 73p 2322_verify_wq2288_kv_secret_paren_regression.md
none of the vendor-prefixed patterns (github/cloudflare/sk/slack/aws) and hits this exact
```

Line 73 is why the false hit happened at all — it contains the body, as prose, with no
prefix in front of it (see §3).

The counts are worse than false; they are unreproducible by construction. wq#2733's notes
say "2 values", wq#2736 got 3, and `mask_replay.py 2732` today reports 23 under the current
table with 9 unaccounted. Nothing regressed between those runs: `corpus('2732')` is 51 live
items (33 `snapshot:`, 7 `read:`, 6 `run:`, 3 `read-stale:`, 1 `transcript:`, 1 `events:`)
drawn from a run directory and a log tree that both keep growing. **A count off a live
corpus is not evidence and must not appear in a closure note.** Closure evidence is a hash,
or a `path:line` at a pinned rev.

## (3) Where the string came from, and the tool gap that stays open on purpose

wq#2736's reconstruction is right. wq#2732's own `detail` field held the prefix and the body
contiguously — exactly once, confirmed by count against `tasks.detail` in the queue db
(never printed; the context around it reads *"...a `<prefix><body>` value ending in `'_'`
leaks its trailing char..."*). It is wq#2732 naming the pattern it was fixing.

`tracked_text()` reads `git ls-files` plus one extra doctrine file (`ISSUE-LOG.md`,
`harness/mask_replay.py:84`), so it has no path to a ticket field and never will account for
this class.

**Ruling: do not teach it one.** `tracked_text()`'s guarantee is "a committed, reviewable
blob"; `tasks.detail` is a mutable sqlite field any worker can write at any time. Admitting
it would let a run clear its own alarm by typing the value into a ticket — a strictly worse
failure than the one this ticket is about. The right move is the house mitigation for a
fixture that arms the tripwire (ISSUE-LOG rows 814/822): split the literal at the prefix
seam. Applied to wq#2732's detail via `wq edit`, verified in both directions:

```
before: glued occurrences = 1
wq edit rc = 0 | edited 2732: detail
after:  glued occurrences = 0 | split present = True
current rules on the new detail: []                  # masking.mask_text, live table
bin/mask-secrets rewrites the new detail: False      # the deployed sed masker
seam-closed accounting survives: wq#2732.detail:1    # mask_replay still sees the whole value
```

So a worker who reads wq#2732 (as doctrine requires) no longer arms the cloudflare tripwire,
and the value stays accountable. Two leftovers, deliberately not fixed here: the **legacy**
table still cuts four values out of that detail (retired 2026-09-20, and the live tripwire
runs the current table), and the frozen `transcript:b58ea02f…` in wq#2732's corpus still
holds the original contiguous text — a replay of that ticket is history and must stay
faithful to it.

## (4) `account()`: prefix adjacency below the floor

The bug, reproduced before the fix against the real tree and against a rev-pinned blob in
another repo (`/tmp/fworker-wq2740/probe_account.py`, committed as
`ops/wq2740-account-prefix-adjacency/probe_account.py`):

```
body sha256[:12] = a745825001bc          glued len = 14  body len = 9  floor = 12
A. pinned foreign blob kb@8d248d2:ISSUE-LOG.md: body occurrences = 19, glued = 0
   account(body, pinned, prefix='cfut_') -> 'kb@8d248d2:ISSUE-LOG.md:127'      # false
   account(body, pinned)                 -> 'kb@8d248d2:ISSUE-LOG.md:127'
B. tracked tree: 2015 files, body in 24, glued in 0
   account(body, tracked, prefix='cfut_') -> '2322_..._paren_regression.md:73'  # false
```

`account()`'s direct path searched the **body alone** and never checked that the rule's
`prefix` sat in front of the hit, so a body that is an ordinary word accounted to 24
unrelated files while the string `_hits()` actually cut — prefix and body glued, the form
`replay()` itself looks for at `harness/mask_replay.py:635` — was in none of them.

The fix requires the prefix immediately before the body **only for a body under
`TRUNCATED_FLOOR` (12 = `burn_filter.MIN_SECRET_LEN`)**, which is not a new policy:
`burn_filter._is_accounted` already tried the whole match first and gated the body-only
retry on the same floor, "so a short body cannot excuse a match by coincidence"
(`harness/burn_filter.py:216`, `:237-239`). `mask_replay.account()` was the sibling that
never got the rule. A *blanket* adjacency requirement was rejected: it would break the
split-at-the-prefix-seam mitigation the whole chain depends on, and it would re-page ISSUE-LOG
row 822's class, where the label wrapped onto the line above its value. Below the floor a
body cannot be a credible secret on its own, so demanding its prefix costs nothing.

After the fix, on the same pinned inputs:

```
A. account(body, pinned, prefix='cfut_') -> None      # no prefix arg: still ':127'
B. account(body, tracked, prefix='cfut_') -> 'reports/2736_verify_wq2733_...cloudflare.md:94'
```

The same holds at `replay()` level, in-process over wq#2732's real corpus and the current
table (`ops/wq2740-account-prefix-adjacency/`, output trimmed to this record):

```
cloudflare: {"value_sha256_12": "a745825001bc", "prefix": "cfut_", "value_len": 9,
             "accounted_in": "reports/2736_verify_wq2733_...cloudflare.md:94",
             "still_matches_today": true, "today_closed": true,
             "first_seen_in": "transcript:b58ea02f-2594-42c1-bf15-5592f37ca89b"}
```

B is a **true** citation and the first one this row has ever had: wq#2736's merged report
quotes the pattern in seam-split form, and the seam pass closes it, so the committed tree
really does hold this value — as documentation, in the file that diagnosed it.

The probe can fail and does discriminate. It reads a rev-pinned blob from a *different* repo
(`/home/lumen/kb` at `8d248d2`), i.e. from outside the population `tracked`'s default is
drawn from, per wq#2630; and the two new tests were re-run against a rebuilt pre-fix
`account()` (`ops/wq2740-account-prefix-adjacency/probe_old_account.py`):

```
old account(short body, prose, prefix)  -> 'doc.md:1'      (new: None -- test discriminates)
old account(floor body, wrapped, label) -> 'reports/r.md:2' (new: same -- floor guard is not a no-op)
```

### Tests

`harness/tests/test_mask_replay.py` — `test_a_short_body_is_only_accounted_with_its_prefix_in_front`
(false-clear closed, at `replay()` level too: the record now lands in `unaccounted()`) and
`test_a_body_at_the_floor_still_accounts_without_its_prefix` (the row-814/822 mitigation and
the wrapped-label case still account). Both pass; the second fails under a blanket
adjacency rule, which is why it is there.

```
$ python3 -m pytest harness/tests/test_mask_replay.py harness/tests/test_mask_triage.py -q
5 failed, 105 passed
$ python3 -m pytest harness/tests/test_burn_filter.py harness/tests/test_masking.py -q
115 passed, 1 xfailed
```

The 5 failures are **pre-existing drift, not this change**: with `harness/mask_replay.py`
restored to `HEAD` in this same worktree they fail identically, down to the assertion
details (`47 == 44`, `[] == ['6d4c27f66a96']`, the same two extra sha12s). They are the
pinned-real-corpus tests (`wq2307`, `wq2336`, `wq2278`, `wq2687`, `wq2702`), and they drift
for the reason §2 gives: they assert value counts and `accounted_in` paths over a live
corpus and a growing tracked tree, so every landed report can move them. Filed as **wq#2755**
rather than re-pinned here: silently re-pinning them erases the only signal they give.

One more thing that replay surfaced, filed as **wq#2756**: under the current table wq#2732's
corpus has 9 unaccounted values, one of them `[BURNED?] github len=36 sha256=a3b99d59dbb0`
from an MCP log snapshot of wq#2732's own glue-leak probe. Its twin on the same line accounts
to `harness/tests/fixtures/wq2702_values.py:12`, so the class is normally a synthetic probe
literal — but that hash is in no ticket, not in `kb/ISSUE-LOG.md`, and not in
`rotated_ledger.json`, so nothing on record says which it is. Given that this very ticket
exists because a row in this chain was closed benign on evidence that did not exist, it gets
triaged, not assumed.

## (5) The "row 824 was still uncommitted" complaint is structural, not sloppiness

wq#2736 noted row 824 sitting uncommitted in the shared kb checkout and filed it as a
separate problem. It is: **no worker can commit `kb/ISSUE-LOG.md` at all.** `/home/lumen/kb`
is a primary tree, so `harness/guard.sh` (wq#900/wq#929) blocks every commit-creating git
verb there, and both escapes it names dead-end for kb — `lumen-pr check /home/lumen/kb` says
"no `origin` remote", and `sync-live` needs a sha on `origin/main`. `bin/session-handoff`
commits only its own handoff paths (`session-handoff:202-216`), never ISSUE-LOG.md. So
doctrine tells every worker to append to a file none of them can land; row 824 was the
predictable result, as was `03f5b49` sweeping 27 rows (807-834) in one batch.

This pass did both halves of the only honest option: the primary's working tree carries the
new rows (that file, not `HEAD`, is the surface doctrine tells workers to read) **and** they
are committed on branch `wq2740-issue-log` in a linked kb worktree (`15f63c8`), so they are
citable by sha. The stale `_kb` branches under six other worktrees show this improvisation is
already the norm. Filed as wq#2759 to pick one sanctioned path — an origin plus the lumen-pr
flow, or a `kb-log append` that commits through a guard-allowed path — and retire the sweep.

## Bottom line

1. `a745825001bc` — **SETTLED, no rotation**, on `sha256('-prefixed')`, not on any file:line.
2. ISSUE-LOG row 824 — corrected by appended row 835 (plus 836 for the drift finding), so the
   next reader of that row does not inherit the false citation.
3. `account()` — fixed with the floor rule `burn_filter` already used.
4. Standing lesson for this chain: **never close a rotation row on a count, or on a citation
   you have not grepped.** wq#2733 had both, and both were wrong in the same 90 seconds of work
   that a hash would have settled for good.
