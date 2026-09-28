# wq#3031 verify-pass: wq#3017 [minerva] wq1614 query-scoped contradiction penalty — CONTRADICT

**Verdict: CONTRADICT.** wq#3017's `_asks_about()` fix is a real improvement over the blanket
multiply the golden gate rejected (wq#3004/`gate_20260928_134734.json`), and every probe the
ticket names — `install.sh --verify` (exit 0), the golden-set gate (`gate_20260928_140308.json`,
PASS/INERT), and `test_contra_penalty.py` (18/18) — passes exactly as claimed. But the scoping
rule it ships is not what its own docstring and tests claim: the `dead_value` arm is a raw
Python substring test (`dv in q_lower`), not the word-boundary match the `subject` arm uses
(`all(w in q_words for w in subj)`). Using the two short `dead_value` rows already present in
the live fixture the gate itself reads, a query that never asks about the dead fact — and does
not even hit the golden set — gets penalized anyway. This is the exact failure shape wq#3017 was
filed to close (qid 142: "a good procedure around one dead fact" losing rank to an unrelated
penalty), reopened through the one code path the fix didn't lock down.

## Reproduction (fresh instance, ops bundle as shipped, no edits)

All three named probes, run unmodified:

```
$ python3 /tmp/.../run_verify.py   # bash ops/.../install.sh --verify, python3-wrapped
                                    # per ISSUE-LOG #987 (bash ops/*/install.sh unlisted)
VERIFY OK
EXIT_CODE=0

$ python3 ops/wq1614-search-v3-contradiction-penalty/test_contra_penalty.py
Ran 18 tests in 1.559s
OK
```

`gate_20260928_140308.json:"verdict"` = `"INERT — penalty is safe but moves nothing"`. Per
REPORT.md §7 the rule fires on 4 of the 257 golden queries (qids 11, 12, 195, 235 — thor-fan and
lumen-ollama) and none of the four change rank. All three probes are consistent with wq#3017's
own done-notes.

## The break

`_asks_about()` (`lumen_recall2.py:466-484`) requires the query to name the contradiction row's
**subject** as whole words (`_words()` tokenizes, then set membership — `"thors deadline"` does
not match subject `"thor"` because `_words` never produces a bare `"thor"` token from it). But
the **dead_value** arm drops straight to `dv in q_lower`, a substring test over the raw query
string with no tokenization and no boundary check — only a `len(dv) >= 2` floor. The live
fixture the gate reads (`/home/lumen/minerva/eval/fixtures/note_contradictions_20260913.json`,
sha `d0df678...499633`, the same file `install.sh` step 0 preflights and step 4 gates against)
already ships three rows with a `dead_value` at or under 4 characters:

```
{'note_id': 'ac50dd3d7452', 'subject': 'thor',       'predicate': 'fan_status',  'dead_value': 'dead'}
{'note_id': '719d13a1483a', 'subject': 'gameboy-pc',  'predicate': 'power_plug',  'dead_value': 'E1'}
{'note_id': '6d29bb85c4b9', 'subject': 'sia',         'predicate': 'status',      'dead_value': 'UP'}
```

Calling the shipped `_asks_about()` directly (no mocks — the actual staged
`ops/wq1614-search-v3-contradiction-penalty/lumen_recall2.py`, imported and run) against these
two real rows with queries that name the subject but never ask about the dead fact:

```python
>>> asks("thor migration deadline notes", "thor", "fan_status", "dead")
True     # "dead" is a substring of "deadline" -- nothing here is about thor's fan
>>> asks("thor deadlock investigation", "thor", "fan_status", "dead")
True     # substring of "deadlock"
>>> asks("sia backup schedule", "sia", "status", "UP")
True     # "UP" is a substring of "backUP"
>>> asks("sia group config notes", "sia", "status", "UP")
True     # substring of "groUP"
>>> asks("sia support ticket triage", "sia", "status", "UP")
True     # substring of "sUPport"
>>> asks("what is thors power plug state", "thor", "power_plug", "off")
False    # negative control: possessive "thors" (no apostrophe) doesn't tokenize
         # to "thor" -- a real false-negative edge too, lower severity
```

(Driver scripts: `/tmp/fworker-wq3031/probe_real_row.py`, `probe_dead_word.py`,
`probe_asks_about.py` — not committed, ticket tmp dir, reproducible from the three rows above and
the shipped `_asks_about` signature.)

Any of these queries, run for real against a corpus where note `ac50dd3d7452` (thor/fan_status)
or `6d29bb85c4b9` (sia/status) is a genuine top-ranked answer to something else entirely (a thor
migration note, a sia backup runbook), gets its score `*= 0.5` for a reason that has nothing to
do with its content. That is precisely the class of harm REPORT.md §1 and wq#3017's own fix say
a soft penalty must not do to "a good procedure around one dead fact" — the fix closes it for
`subject` and reopens it for `dead_value`.

## Why the shipped probes can't see this

- **`install.sh --verify`** only `vcontains`-greps for the new function signature and call sites
  (`install.sh:20-28`) — it cannot detect a scoping-logic regression, by construction.
- **The golden-set gate** is measurably the wrong instrument here: REPORT.md §7 states the rule
  fires on only 4/257 golden queries, and none of those four happen to hit a short, common
  `dead_value`. `gate_20260928_140308.json`'s "PASS/INERT" is real but is evidence about 4 fixed
  queries, not about the `_asks_about` boundary in general — this is exactly the "probe drawn
  only from the population the fix was designed around confirms and never falsifies" trap this
  fleet's own doctrine calls out (wq#2290/wq#2630).
- **`test_contra_penalty.py`** tests `dead_value` scoping with only long, low-collision strings
  (`'seized'`, `'LIVE-VERIFIED'`) — never the short values (`dead`, `E1`, `UP`) the real fixture
  ships today. `test_asks_about_word_boundaries` (test file line 298) asserts a boundary property
  for **subject** (`"llama33 status"` half-subject correctly fails) but has no equivalent case for
  `dead_value`, so the asymmetry between the two arms was never exercised.

## Scope of the miss

Not every row is exploitable — `gameboy-pc`/`power_plug`/`E1` is a much narrower target string
than `dead`/`UP`/`on`/`off`. But 2 of the 3 short rows in the *current* live fixture (10% of all
20 rows) collide with ordinary English on the first two-and-three-word queries tried. `dead_value`
is domain data (hardware/service state strings), and short binary-state values (`on`/`off`/`up`/
`down`) are the norm for that domain, not an edge case — `power_plug` and `status` are two of the
five predicates the wq#1610 scan already covers, and both are exactly the predicates that produce
short values.

## What I did not do

Did not re-implement or patch `_asks_about`. The doctrinally obvious fix — tokenize `dead_value`
the same way `subject` already is, and require whole-word (or whole-phrase, for multi-word values
like `LIVE-VERIFIED`) containment instead of a raw substring test — is a judgment call about
where the line sits for multi-token values, so it goes to the escalation this contradiction
triggers, not into this verify-pass.

## Disposition

`wq#3031` closes as CONTRADICT. Filing `[judgment] wq#3031 verify-pass CONTRADICTED wq#3017`
(opus-5@high per the archetype-8 escalation rule) with this report cited as evidence, and
`/home/lumen/minerva/lumen_recall2.py` stays on the wq#3017 file it is on now — nothing here
argues for reverting the install, only for tightening the `dead_value` arm before the fleet
leans on it further. `wq#3030` (index coverage) is unaffected and orthogonal.
