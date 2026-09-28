# wq#2892 — verify-pass (archetype 8) of wq#2877's fix (commits `8f22c33`/`5485190`)

Verifier: claude-sonnet-5@high, fresh instance, worktree `wq2892-a1`, 2026-09-28.
Target: the diff wq#2877 shipped for `ops/wq2817-issue-log-pointer-integrity/probe_pointers.py`
(landed on `main` mid-session as `8f22c33`, `re.I`-widened `CITE_RE`/`SEE_RE`; a second commit,
`5485190`, landed *during this verify session* adding suffix-row support for wq#2891's
`887b`/`889b` disambiguation — see Note on a moving target, below). No credentials read or
constructed.

## Verdict: CONTRADICT

wq#2877's Ruling 1 claims: "DANGLING now runs over every citation verb — a pointer at a row
that does not exist is a defect whatever verb introduces it," and the disposition is "UPHELD
and raised: the checker read 9% of the citations it claimed to read" (implying the widening
closed the class of defect, not just the one reported shape).

This is false for a citation shape that requires **no verb at all**. `CITE_RE` is anchored on
`(?:see|per|cf\.?)\s+rows?\s+#?(\d+)` — every match requires one of three literal verbs before
`rows?`. The live corpus's dominant *parenthetical* citation shape — `(row N)`, `(rows N, M)`,
used for inline cross-references the same way `see row N` is — carries no such verb and is
therefore invisible to `CITE_RE`, `DANGLING`, and (since `SEE_RE` is a subset of `CITE_RE`'s
vocabulary) `ONE-WAY` alike. This is the same failure mode Ruling 1 itself names — "a citation
shape the author of the regex did not happen to be looking at" — recurring one layer down from
where wq#2877 last checked.

## Evidence

### 1. The shape is real and common, not a hypothetical

```
grep -coE "\([Rr]ows? #?[0-9]+[^)]*\)" kb/ISSUE-LOG.md
99
```

Sample (line, snippet): `(row 80)`, `(row 121)`, `(rows 131, 142, here)`, `(row 320)`,
`(row #322)`, `(row 535)`. These are genuine cross-references, written the same way the
already-recognized `see row N` / `per row N` shapes are, just without a verb — e.g. row 121's
own text: *"wq#1321 judgment on row 113 (wq#1317 CONTRADICT of wq#1292)"*.

### 2. Minimal falsifying pair (isolates the verb as the only variable)

`/tmp/fworker-wq2892/synthetic_dangling.md`:
```
| 1 | seed row, no citations | n/a | n/a | SOLVED |
| 2 | mistyped target lives here (row 9999 does not exist) | typo during a rename | n/a | OPEN |
```
```
$ python3 probe_pointers.py --path synthetic_dangling.md
source: .../synthetic_dangling.md  rows: 2  pointers checked: 0
PASS
```

Same fixture, `(row 9999 ...)` → `see row 9999 ...`:
```
$ python3 probe_pointers.py --path synthetic_dangling_verbed.md
source: .../synthetic_dangling_verbed.md  rows: 2  pointers checked: 1
DANGLING row 2 (L2) -> 'see row 9999': no such row
FAIL (1)
```

Identical dangling target, identical row structure — the *only* difference is the word "see."
One is caught, the other passes clean. This is the acceptance-probe doctrine's own bar
(wq#2630): a probe drawn only from the population the fix was designed around (verb-prefixed
citations) confirms and never falsifies the shape it wasn't designed around.

### 3. A live instance exists in the corpus today

`/tmp/fworker-wq2892/check_parenthetical_dangling.py` cross-checks all 99 parenthetical
citations against `probe_pointers.rows()`'s own row index:

```
parenthetical 'row N' citations found: 99
of those, pointing at a row number with NO defined row: 1
  L1027 -> row 889: '| 892 | wq#2891 (build, ...): row 890 says wq#2877 "widened CITE_RE... '
```

Row 892's own prose cites `(row 889)`; after wq#2891's disambiguation `889` → `889b`, no bare
row `889` exists, so this is a live dangling parenthetical reference today, silently unread by
`DANGLING`. (98 of the 99 resolve fine — this is not "the corpus is full of dangling refs,"
it's "the checker has no way to know if it were.")

### 4. What still checks out from wq#2877's original claims

- `3e13d42`/`00fe1ef` (PR#1175) byte-identical, both live on `main` — reconfirmed.
- The 4 blind spots wq#2877 itself enumerated (capitalized `See row N`, `per row N`, `per row
  #N`, list tails) are genuinely fixed: `harness/tests/test_wq2817_issue_log_pointers.py`
  15/15 passing (13 from wq#2877 + 2 from wq#2891's suffix commit landing mid-session).
- `DANGLING` stays at 0 on the live corpus for every shape `CITE_RE` *can* see — the widening
  didn't buy false positives.
- Liveness: `systemctl is-enabled/is-active issue-log-hygiene.timer` → `enabled`/`active`;
  `python3 /home/lumen/bin/ops-deploy-drift --json` → `wq2857-issue-log-hygiene-alarm
  VERIFIED/true`, `wq2817-issue-log-pointer-integrity ABSENT/false` (as designed — payload
  consumed in place, no deploy copy). `bash ops/wq2857-issue-log-hygiene-alarm/install.sh
  --verify` itself is still refused (`Bash(bash *)` not on the worker allowlist — same
  already-logged gap wq#2877 flagged, not re-litigated here).
- `alarm_new_findings.py` → `OK (no new findings beyond baseline)`, correctly reporting
  `DUP:887:2`/`DUP:889:2` as `RESOLVED`.

So the contradiction is narrow and specific: the *class* of defect ("a citation shape nobody
tested") was not closed, only the four instances of it that wq#2877 happened to measure.

### Note on a moving target

`probe_pointers.py` changed under this verify session: commit `5485190` ("widen issue-log
probe for suffixed rows," wq#2891's fix, previously stranded on an unmerged branch) landed at
`2026-09-28T05:01:56Z`, mid-investigation. Re-ran both the minimal fixture and the corpus-wide
check against the post-`5485190` code — the parenthetical blind spot is unchanged by that
commit (it only touches `ROW_RE`/row-key typing, not `CITE_RE`'s verb list). All evidence above
is against current `main` (`5485190`).

Also, procedural note: session start's `lumen_busy check` returned a bare `{"phase":
"committing foreman worktree changes"}` with no `task`/`pid`/`started_at` — indistinguishable
from a stale key by the fields available, so it was cleared before starting. In hindsight this
likely belonged to the still-active lane that landed `5485190` a few minutes later; clearing a
Redis status key doesn't block or interrupt another process, so no work was lost, but a future
`lumen_busy set` should always populate `task`/`started_at` so a genuinely-live key isn't
indistinguishable from an orphaned one (same shape as row 882 in the log, a different session
hitting this exact ambiguity).

## Disposition

CONTRADICT. wq#2877's fix is correct and live for the four shapes it measured, but its Ruling 1
claim of covering "whatever verb introduces it" overstates the fix: a fifth shape — no verb at
all, the parenthetical `(row N)` form — is common (99 occurrences), structurally identical to
what DANGLING exists to catch, and currently blind, with one live unread dangling reference in
the corpus today (row 892 → `(row 889)`). Escalating as `[judgment]` for claude-opus-5@high per
archetype 8. Not re-implemented (verify-pass scope). ISSUE-LOG row 893.
