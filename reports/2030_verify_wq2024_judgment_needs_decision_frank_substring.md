# wq#2030 — verify-pass on wq#2024 / PR #648 ("label defers by TYPE so Frank can skip the ones not worth reviewing")

**Verdict: CONTRADICT.** PR #648's `_NEEDS_DECISION` regex
(`ops/wq2024-label-defer-types/defer_type_labels.py:35-39`) includes the bare
alternative `frank` with no word boundary. Because `classify_defer_type`
checks this pattern *first*, in priority order, any `decision_reason` that
merely contains the substring "frank" — e.g. the ordinary English word
"frankly", or "Frankenstein" — is misclassified as `defer-needs-decision`,
regardless of what the rest of the text says. `defer-needs-decision` is the
one label wq#2024 says should be Frank's *default, filtered* review queue
("Frank's review queue should show ONLY `defer-needs-decision` by default").
So a defer that is plainly `defer-theoretical` by every other signal in the
text — "paper-focused", "proof of concept only", "promising but unproven in
production", all phrases the classifier itself matches correctly when
"frank(ly)" isn't also present — gets shown to Frank anyway. This is the
exact failure mode the ticket was opened to stop: "he keeps opening deferred
proposals and finding they were dismissed on theoretical/untested grounds —
wasting his review time."

## What PR #648 claims

`README.md` and the 38-test suite (`test_defer_type_labels.py`, 38/38 pass,
independently re-run here unchanged) claim the classifier correctly
prioritizes `defer-needs-decision` first specifically so genuine
Frank-facing reasons "never gets swallowed by 'defer-theoretical'" (module
docstring, `defer_type_labels.py:25-33`). The priority-order design is sound
and well-reasoned; the bug is narrower and orthogonal to that design.

## Break attempt

`_NEEDS_DECISION` (`defer_type_labels.py:35-39`):

```python
_NEEDS_DECISION = re.compile(
    r"frank|needs?\s+(a\s+)?decision|design\s+(call|decision)|waiting\s+on\s+(a\s+)?(frank|human|owner)|"
    r"needs?\s+(a\s+)?(product|policy|architecture)\s+call|your\s+call|human\s+judg",
    re.I,
)
```

The first alternative, `frank`, has no `\b` anchors on either side — unlike
`_NEEDS_HARDWARE`'s `no\s+(dedicated\s+)?gpu\b`, which does. `re.search`
matches it as a substring anywhere in the text, including inside unrelated
words.

Every test case in `test_defer_type_labels.py` that names Frank spells it as
a standalone word ("Needs a design decision from Frank...", "This is
Frank's call..." — grep confirms zero occurrences of "frank" as a
substring-only collision anywhere in the test file), so this was never
exercised.

Ran three decision_reason strings, each phrased directly from the ticket's
own `defer-theoretical` reason-cluster wording ("paper-focused",
"proof of concept only", "promising but unproven in production",
"academic"), each containing "frank" only as part of an unrelated word:

```
$ python3 reports/2030_probe_needs_decision_frank_substring.py
BUG  classify('A paper-focused proof of concept only, frankly not production ready.') -> 'defer-needs-decision'
BUG  classify('Frankenstein-style hack stitched from two papers, promising but unproven in production.') -> 'defer-needs-decision'
BUG  classify('This is frankly too experimental and academic to ship as-is.') -> 'defer-needs-decision'

3/3 cases misrouted into Frank's default queue
```

All three should be `defer-theoretical` (confirmed: stripping the word
"frank"/"frankly" from each string and re-classifying returns
`defer-theoretical` in every case — the rest of the pattern set works as
designed).

## Why this matters for the verdict

This isn't a hypothetical edge case gated behind unusual input — "frankly"
is a common hedge word in written English, plausible in real LLM-generated
or human-written `decision_reason` text in the actual 316-row defer pile the
ticket references (untested here, per PR's own README: sandbox can't reach
`minerva.db`, backfill only ran against a synthetic fixture). Because
`defer-needs-decision` is checked *first* in priority order and is also the
*only* label the ticket says should surface by default, this bug doesn't
just mislabel a row — it actively defeats the ticket's stated purpose for
every row it hits: a theoretical/unproven defer that should be filtered out
of Frank's view instead lands directly in it.

## Not in scope / not reproduced as new findings

- The same unanchored-substring shape exists in the `waiting\s+on\s+(a\s+)?
  (frank|human|owner)` alternative later in the same regex (e.g. "waiting on
  a frankenstein-like patch" would also match) — same root cause, not
  separately demonstrated since it's strictly dominated by the bare `frank`
  alternative already covered above (anything the second collides on, the
  first already catches).
- Spot-checked the other six label patterns for the same missing-`\b` shape
  (`insufficient\s+ram`, `no\s+cuda`, `academic`, `missing\s+info`, `prereq`)
  — all substring-tolerant by construction but none collide with a common
  English word the way "frank" → "frankly"/"Frankenstein" does; not raised
  as findings.
- Did not re-verify `backfill_defer_labels.py` (idempotency, additive
  column, `--dry-run`, duplicate-suspect note) — read it, no bugs found
  (`id` is `TEXT PRIMARY KEY` per the schema precedent the PR itself cites
  from `ops/wq1500-minerva-promoter/test_minerva_promote.py`, so the
  `", ".join(...)` calls on row ids are safe, not the int/str crash they'd
  otherwise be).
- Not evaluating whether landing a classifier-only module (no
  `minerva_defer_review.py` / mission-control wiring) is an acceptable
  partial delivery for this ticket — `kb/ISSUE-LOG.md` #430 and the wq#2024
  done-notes already document that gap transparently as a sandbox-reach
  limitation with a named follow-up, not a false completion claim. Not
  re-litigated here.

## Reproduction

`reports/2030_probe_needs_decision_frank_substring.py` imports
`defer_type_labels.classify_defer_type` directly (no DB, no fixture) and
prints per-case pass/fail — output pasted verbatim in "Break attempt" above.

Not implementing a fix here — this is a verify-pass, not a re-implementation.
Fix shape for the escalated judgment: anchor the bare alternative with `\b`
(`\bfrank\b`) the same way `_NEEDS_HARDWARE`'s `gpu\b` already does, add a
`frank`-as-substring decoy case (e.g. "frankly") to
`test_defer_type_labels.py` as a true `defer-theoretical` case, and re-run
both the existing suite and this probe before the label is trusted for the
live backfill.
