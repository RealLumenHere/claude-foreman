## What

wq#2151. The contradiction scanner matched `isis.service_state`'s dead value `UP` against
the English preposition: on 2026-09-20, 8 of its 9 corpus hits were prose — *"Spin up a
FastAPI orchestrator"*, *"Set up the Docker container"* — in research sections that happened
to mention Isis and a service.

**Half of that was fixed by wq#1775 while this ticket sat in the queue**, and this PR is
smaller than the ticket because of it. wq#1775's corpus tier added `SHOUTED_SHORT_RE`: a
2-4 character all-caps value must be shouted in the prose too, corpus-only. Same diagnosis,
same evidence (§8.1), different lane, landed first. What it deliberately left alone is the
fleet tiers, in its own test's words: *"Fleet artifacts keep the case-insensitive match they
were measured with."*

This PR ships the other half: a dead value that is an ordinary English word
(`COMMON_WORD_VALUES`) matches **case-exactly in every tier**, not just the corpus.

## Why

Not wq#1781 recurring. The value was correctly scoped by `self_identifying()` (len 2 < 8);
the scope was the bug — subject-plus-cue is no scope for a word any prose about the subject
contains. wq#1775's narrowing stops at four characters and never reaches notes or fleet
YAML, so `ONLINE`, `RUNNING` and `bes.yaml`'s `DOWN` were still live. On the tiers where
this scanner's hits are usually real, a false `bes.status = 'DOWN'` is a ticket filed
against a correct file.

## How

`COMMON_WORD_VALUES` + `common_word()` + `boundary_re(case_exact=)`. Nothing else about
scope changes: subject and predicate cue may still both come from the enclosing section, so
`isis:` / `  service_state: UP` in a YAML artifact still reports. `SHOUTED_SHORT_RE` stays
exactly as wq#1775 shipped it and still carries the non-word remainder (`OK`, `EOL`, `RMA`).

## Testing

`python3 -m pytest ops/wq1609-minerva-contradiction-scan ops/wq1629-minerva-value-note-split`
— **90 pass**. New: the prose-preposition case from the real hit list, the case-exact
boundary rule, the word-not-length membership test, the `bes.yaml` shape (a YAML prose field
vs a quoted status), and the changelog suppressor's matching case rule.

Two of wq#1775's tests are touched, both deliberately:

- `test_a_short_shouted_value_must_be_shouted_in_the_prose_too` asserted the fleet tiers stay
  case-insensitive *because unmeasured*. They are measured now (below), so the assertion is
  inverted and the reason recorded. Its two in-test fact inserts are dropped in favour of the
  fixture's `f13`/`f14`, which are the same fact — with both, the index held it twice.
- `test_prose_subject_may_not_come_from_an_enclosing_heading` is untouched, and is why
  option (b) below was dropped.

Measured, read-only, nothing written to either store:

| | main | this branch |
|---|---|---|
| corpus action hits | 0 | 0 |
| corpus index entries · prefilter docs | 87 · 3,617 | 87 · 3,617 |
| daily scan hits (notes + fleet artifacts) | 32 | **28** |

The four removals, none of them real, nothing added:

- `archival_notes:6d29bb85c4b9` — `sia.status = 'UP'` against *"- 3 containers up"*
- `LIVE_FLEET_STATE.md:19,20` (info) — the word inside two hardware inventory lines
- `fleet/inventory/machines/bes.yaml:7` — `bes.status = 'DOWN'` inside
  `alert_desc: "… vault standby slot down"`

## Risk / rollback

Precision-only change to a read-by-default scanner; the index, the schema and the `--apply`
tiers are untouched, and corpus reach is unchanged at 0 hits either way. The exposure is
recall: a status genuinely written in a case the fact did not record (`service_state: up`
against a recorded `UP`) is now missed in the fleet tiers as it already was in the corpus.
Measured against the live store — four removals, all false, none real.

Rollback is `git revert`; the change is confined to matching logic plus docs and evidence.
Deployment is `ops/wq1609-minerva-contradiction-scan/install.sh`, which runs the suite
before it installs; leaving it undeployed just leaves the 06:20 timer on the old matcher.

Option (b) from the ticket (predicate cue on the matched line) was implemented, measured and
**dropped**: it removed one further corpus hit that case-exactness removes anyway, zero fleet
hits, and it broke `test_prose_subject_may_not_come_from_an_enclosing_heading`. Option (c),
a length gate, is rejected — `P3` and `OK` are as short as `UP` and harmless, `free` and
`live` are longer and not. Reasoning in `research/PGVECTOR_INVALIDATION_CONTRACTS.md` §8.2;
evidence files in `ops/wq2151-contradiction-scan-common-words/`.

Refs: wq#2151

🤖 Generated with [Claude Code](https://claude.com/claude-code)
