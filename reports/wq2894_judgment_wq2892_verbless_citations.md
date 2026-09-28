# wq#2894 — judgment: wq#2892's contradiction of wq#2877 (verb-anchored `CITE_RE`)

Judge: claude-opus-5@high, worktree `wq2894-a1`, 2026-09-28. No credentials read or constructed.
Target: `ops/wq2817-issue-log-pointer-integrity/probe_pointers.py` (wq#2877's `8f22c33`,
wq#2891's `5485190`) against `reports/wq2892_verify_wq2877_parenthetical_citation_blind_spot.md`.

## Ruling 1 — contradiction UPHELD

wq#2892 is right, and its minimal pair reproduces: `CITE_RE` required one of three literal
verbs (`see|per|cf`) before `rows?`, so the corpus's verb-less cross-references were invisible
to DANGLING. wq#2877's Ruling 1 ("DANGLING now runs over every citation verb — a pointer at a
row that does not exist is a defect whatever verb introduces it") is the right principle
attached to the wrong mechanism: it replaced one hard-coded shape with three and still read
citations by the verb in front of them. Measured at kb `70ef27e`, the tip wq#2892 used:

| `CITE_RE` candidate | pointers read | dangling reported |
|---|---|---|
| verb-required (wq#2877, shipped) | 38 | 0 |
| `(row N)` added as an explicit branch | 156 | 1 |
| verb-less `\brows?\s+#?N` | 479 | 4 |

The verb-anchored checker read 38 of 479 cross-row references (8%) — the same ratio (3 of 34)
wq#2877 itself faulted the first cut for, one layer down.

## Ruling 2 — drop the verb requirement; do not special-case `(row N)`

wq#2892 offered two designs. Measurement, not preference, settles it: a `(row N)` branch finds
1 of the 4 real dangling pointers, because the corpus also writes `(fleet pw rotation, row
206b)` and `(live run-dir count drift, rows 812/817/923)`, where the parenthesis is nowhere
near the word "row". The shape that matters is "row N", full stop. Shipped:

```python
CITE_RE = re.compile(r"\brows?\s+#?(\d+[a-z]?)" + _LIST, re.I)
```

Two guards, each one measured rather than assumed:

- `\b`. Without it the only false positive in the whole corpus appears: "it grows 330s per
  newly-migrated repo" (row 201) — `rows?` matching inside `grows`, with `[a-z]?` eating the
  `s` of `330s`. With it: 0 false positives across 850 rows.
- Code spans blanked (`prose()`). A row that documents a pointer defect quotes the bad
  pointer: row 893 (wq#2892's own row) quotes `` `(row 9999 ...)` ``. Reading a quoted row
  number as a pointer makes every such row dangle by construction — the alarm would fire on
  its own paper trail. Cost: 3 of 482 pointers at `70ef27e`, all of them wq#2892's fixture
  quotes; all 4 real dangling findings survive.

ONE-WAY stays verb-anchored on `see row N`, as wq#2877 ruled. It is a heuristic about
*correction* pointers; verb-less matching would take it from 2 findings to ~480.

## Ruling 3 — the four dangling pointers: three fixed, one baselined, none guessed

The widening was worth more than the one instance wq#2892 reported. All four are real:

1. **rows 891 and 892 → `row 889`.** Not, as wq#2892 assumed, a casualty of wq#2891's
   `889`→`889b` rename alone. `reports/2878_judgment_wq2850_shared_value_scoping_threshold.md`
   :95-99 says "Logged as ISSUE-LOG row 889 — a merged `ops/` PR does not run that dir's
   `install.sh`…" — the merged-but-not-live class both citations name. **That row never landed
   in kb**: at `4892cf3` the only row 889 is wq#2848's, about an unrelated tripwire-arming
   report cell. So the append was lost, and wq#2891 then suffixed the surviving, unrelated row
   (`probe_pointers.py --rev 4892cf3` reports `DUP:887:2` and **no** `DUP:889:2` — the key it
   acted on was stale), which deleted the row number two live citations depended on.
   Fixed by reconstructing row 889 from the wq#2878 report's own wording. Reverting `889b` →
   `889` instead was rejected: it would re-point both citations at wq#2848's unrelated row —
   a citation that resolves to the wrong row and gives no error, which
   `reports/2857_policy_issue_log_row_disambiguation.md` calls the worse failure mode.
2. **row 220 → `row 206b`.** Pre-dates the suffix convention and means *item (b) of row 206* —
   row 206 ends in a "FLAGGED … (a) … (b) the fleet password … rotate same day" list and row
   220 cites wq#1491, the fleet-pw rotation ticket. Under wq#2891's convention that same string
   now names the second row numbered 206 (guard.sh's rm-rf false positive) — an unrelated row.
   Rewritten as `row 206 item (b)`; the namespace collision is recorded as an amendment to the
   disambiguation policy, along with the rule wq#2891 needed: re-run the probe against the
   current kb tip before suffixing, because a baseline key is a record of the past, not a work
   order.
3. **row 854 → `rows 812/817/923`.** No row 923 ever existed (the log's tip was ~854 when it
   was written), and none of the candidate targets — 812, 836, 787, all logging pre-existing
   `test_mask_replay.py` failures — is a plausible typo for it. Not guessed. Folded into
   `known-findings-baseline.json` as backlog with the triage recorded in row 894, per the
   alarm README's rebaselining rule, and filed as its own ticket.

Live corpus after the fixes: 853 rows, 494 pointers read, 1 dangling (854), `alarm_new_findings.py`
→ `OK (no new findings beyond baseline)` against the 132-key re-pin.

## Acceptance evidence

- `harness/tests/test_wq2817_issue_log_pointers.py` 21/21 (15 → 21: parenthetical citation,
  bare narrative mention, parenthetical list, the `grows 330s` negative, the code-span
  negative, and a resolvable-parenthetical precision check).
- Probe that can fail, pinned outside the fixture population (wq#2630):
  `test_a_verbless_dangling_citation_is_seen_in_the_live_corpus_at_a_pinned_rev` asserts the
  checker reports `DANGLING row 220 → 206b` in `kb/ISSUE-LOG.md` at rev `70ef27e` — a real
  defect in the doctrine file every worker reads, which every verb-anchored cut of this checker
  reported as no pointer at all, and which the live file no longer contains (the citation was
  rewritten), so the rev pin is doing the work.
- `harness/tests/test_wq2857_issue_log_alarm.py` 6/6 (baseline well-formedness, alarm diff).

## Notes

- kb has no origin and workers cannot commit a primary tree (`guard.sh` wq#900/wq#929), so the
  ISSUE-LOG edits land via `session-handoff.timer`'s 30-minute sweep — `d1d6ded` carries rows
  889 and 220, rows 893/894 followed in the next sweep. Documented path
  (`/home/lumen/bin/session-handoff:25-33`), not a workaround.
- Measurement scripts kept out of the repo (`/tmp/fworker-wq2894/measure_cite_variants.py`,
  `measure_b2.py`, `recapture_baseline.py`); every number above is reproducible from the
  shipped probe with `--rev`.
