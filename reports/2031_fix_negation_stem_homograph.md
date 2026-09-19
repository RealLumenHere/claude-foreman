# wq#2031 — fix: `route_conflict()` negation stems that are real words

**Verdict of wq#2028 (CONTRADICT) upheld and fixed.** PR#646 (wq#2025) introduced a false
negative on the dedup gate's core job.

## The defect

`_NEGATION_RE` accepted apostrophe-less `<stem>nt` spellings for an enumerated list of
auxiliaries. The comment shipped with that list justified it as safe because those stems'
bare spelling is "unambiguous", in contrast to a fully general `\w+n'?t`, which

> swallows ordinary words ("persistent", "different") and would suppress REAL conflicts,
> a worse failure than the one being fixed.

Two members of the list fail that test. `ca` + `nt` = **cant** (jargon; a slanted surface,
as in the cant of a roof or a rail bank). `wo` + `nt` = **wont** (a customary habit). Both
are ordinary English words, so an add-intent clause containing either was read as negated
and the duplicate was dropped:

```
cant -> (False, None) | Add a cant-adjusted route named bge-m3 to the litellm model pool as a new entry.
wont -> (False, None) | Register the wont pattern as a new litellm route named bge-m3 in the model pool.
```

Both are genuine duplicate-route-add proposals for an existing route (`bge-m3`) and must
return `(True, 'bge-m3')`. `cant` was not in the pre-#646 list at all, so #646 introduced
that collision outright; `wont` predates it via `won'?t`, but #646 newly asserted the whole
bare-stem class was safe without checking it.

The safety claim was asserted in prose, never tested. Checking it costs one line:
`/usr/share/dict/words` (102,485 entries) contains exactly two of the 21 bare spellings —
`cant` and `wont`.

## The fix

`ops/wq2019-proposal-reviewer-fix/minerva_dedup.py:175` (pattern at 173-178, rationale at
146-172) — drop `ca` and `wo` from the bare-stem arm. Nothing else moves:

- `can't` / `won't` were always the general `\w+n't` arm's job.
- `cannot` remains a literal alternative.
- bare `not` covers `can not` / `will not`.

**Cost of the trade, stated plainly:** an apostrophe-less typo of `can't` ("this cant add a
new route") now reads as a conflict. That is a false positive a human reviewer sees and
clears, not a real duplicate silently dropped on the floor — the direction the module's own
comment already calls the cheaper failure. The false-positive case asserting bare `cant`
cleared has been removed from `test_route_conflict.py` accordingly, and `can not` / `won't`
cases added in its place.

## The standing gate

`reports/2031_probe_negation_stem_homograph.py` is the probe the ticket asked for, written
so it cannot rot:

1. It reads the bare spellings **back off the compiled pattern** rather than restating them,
   so it tracks any future edit to `_NEGATION_RE` automatically.
2. Dictionary check: no accepted bare spelling may appear in the system word list.
3. Behaviour check: each accepted bare spelling, used as a decoy word inside the add-intent
   clause of a real route-add proposal, must actually suppress it. This is what makes (2)
   load-bearing — a spelling that suppresses *and* is a real word is a live false negative.
4. Regression checks: `cant`/`wont` decoys are now caught as `bge-m3` duplicates, and
   `can't` / `won't` / `cannot` / `can not` still suppress.

Falsification, since a probe that cannot fail proves nothing — with the pre-fix pattern
restored in memory (`ca`/`wo` re-added), the probe goes red on all four rows:

```
FAIL:
 - HOMOGRAPH: bare 'wont' is an ordinary English word, ...
 - HOMOGRAPH: bare 'cant' is an ordinary English word, ...
 - REGRESSION: 'cant' decoy still suppresses a real route add -> (False, None)
 - REGRESSION: 'wont' decoy still suppresses a real route add -> (False, None)
EXIT= 1
```

## Test results

| command | result |
| --- | --- |
| `python3 ops/wq2019-proposal-reviewer-fix/test_route_conflict.py` | `OK: 16 false positives cleared (13 ... reach the add-intent window logic ...), 8 true positives still caught` |
| `python3 reports/2031_probe_negation_stem_homograph.py` | `OK: 19 bare spellings vs 102485 dictionary words; ... none is a dictionary word` |
| `python3 reports/2028_probe_negation_stem_homograph.py` | `cant -> (True, 'bge-m3')`, `wont -> (True, 'bge-m3')` (was `(False, None)`) |
| `python3 reports/2023_probe_negation.py` | unchanged — all five contracted modals still suppress |

`reports/2025_probe_negation_contraction.py` is left untouched: it evaluates candidate
patterns embedded in itself rather than importing the shipped module, so it is a dated
record of the wq#2025 decision, not a live gate. Its `CHOSEN` pattern is the one this
ticket amends.

## Generalisation

The bug class is not "regex". An enumerated safe-list whose safety argument is a *property
of its members* — "unambiguous", "never collides", "always numeric" — needs a test that
checks the property per member. A comment asserting it is how `cant` got in. Logged as
ISSUE-LOG #432.
