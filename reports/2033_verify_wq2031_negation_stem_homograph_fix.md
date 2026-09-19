# wq#2033 — verify-pass: wq#2031's `route_conflict()` negation-stem homograph fix

**Verdict: CONFIRM.** Adversarial re-check of the diff shipped by wq#2031 (PR#652,
commit `fd2ecb6`). The fix does what it claims: it drops the two bare `<stem>nt`
spellings (`cant`, `wont`) that collide with real English words from `_NEGATION_RE`'s
enumerated arm, and it backs the "the rest are unambiguous" claim with a probe that
actually checks the property per member instead of asserting it in prose again.

## The diff

`ops/wq2019-proposal-reviewer-fix/minerva_dedup.py` +12/-1 (comment + one regex line),
`test_route_conflict.py` +15/-3, plus two new report files (`2031_fix_...md`,
`2031_probe_negation_stem_homograph.py`). Net code change: `ca` and `wo` removed from
the bare-stem alternation at `minerva_dedup.py:175`, leaving 19 stems
(`do|does|did|is|are|was|were|sha|has|have|had|could|would|should|must|might|need|
ought|ai` + `nt`).

## Break attempts

**1. Re-ran the exact wq#2028 repro against the shipped fix** (this is the case the
fix is supposed to close):

```
$ python3 reports/2028_probe_negation_stem_homograph.py
cant -> (True, 'bge-m3') | Add a cant-adjusted route named bge-m3 to the litellm model pool as a new entry.
wont -> (True, 'bge-m3') | Register the wont pattern as a new litellm route named bge-m3 in the model pool.
```

Both now correctly return `(True, 'bge-m3')` — was `(False, None)` pre-fix. Closed.

**2. Checked whether the fix over-claims "unambiguous" for the 19 stems it left in,
the same way wq#2025 over-claimed it for all 21.** Rather than trust the shipped
probe's own dictionary check, re-derived the bare spellings independently
(`dont, doesnt, didnt, isnt, arent, wasnt, werent, shant, hasnt, havent, hadnt,
couldnt, wouldnt, shouldnt, mustnt, mightnt, neednt, oughtnt, aint`) and confirmed via
the probe's dictionary pass (`/usr/share/dict/words`, 102,485 entries) that none
collide — including candidates that *look* closest to real words (`shant` vs.
`shan't`, `havent` vs. the real word `haven`, `aint` vs. informal `ain't`): none of
these bare 6-letter-or-shorter concatenations are themselves dictionary entries.
`bare_spellings()` reads the list back off the compiled pattern rather than a
hand-copied literal, so this check tracks the shipped regex, not a stale restatement
of it.

**3. Ran the full regression chain** (all pre-existing negation probes, not just the
one wq#2031 added):

```
$ python3 ops/wq2019-proposal-reviewer-fix/test_route_conflict.py
OK: 16 false positives cleared (13 ... reach the add-intent window logic ...), 8 true positives still caught
$ python3 reports/2031_probe_negation_stem_homograph.py
OK: 19 bare spellings vs 102485 dictionary words; all 19 bare spellings suppress and none is a dictionary word; cant/wont decoys caught as bge-m3 duplicates; can't/won't/cannot/can not still suppress
$ python3 reports/2025_probe_negation_contraction.py
CHOSEN  false positives 0/12  false negatives 0/4
$ python3 reports/2023_probe_negation.py
(all five contracted-modal disclaimers still return (False, None), unchanged)
```

Zero regressions across the whole wq#2019→wq#2031 negation-handling lineage.

**4. Checked for stale references and other consumers.** `grep -rn "cant|wont"` across
`ops/wq2019-proposal-reviewer-fix/` turns up only the new comment/test lines added by
this diff — no leftover assertion anywhere still expects the old (buggy) behavior.
`route_conflict`/`minerva_dedup` is imported outside that directory only by
`pilots/0eed2f5bdb92/{run_pilot,build_sample}.py`, and only for the unrelated
entity-share/pgvector dedup path (`minerva_dedup.check`), not `_NEGATION_RE` — not
affected by this diff.

**5. Sanity-checked the regex mechanics**, since `\b` word-boundary reasoning is easy
to get subtly wrong: `\b` wraps the whole bare-stem alternation, so a bare spelling
can only match as a complete token (non-word char or string edge on both sides) — it
cannot fire as a substring inside a longer real word (e.g. no accidental match of
"isnt" inside some unrelated token), and the general `\w+n't` arm still independently
covers `can't`/`won't` regardless of what the enumerated arm contains, since both end
in the literal `n't` — confirmed live by check 3's `2023`/`2025` probe runs, which
exercise contracted modals through that arm.

No collision, regression, or scope gap found in the diff itself. The one adjacent
weakness on record — apostrophe-variant normalization (acute accent, backtick, prime
mark, etc. not matching `\w+n't`) — was flagged out-of-scope by wq#2028's verify-pass
and untouched by wq#2031 by design (wq#2031's ticket was the stem-homograph gap, not
apostrophe normalization); not re-raised here as a new finding against this diff.

## Conclusion

wq#2031's diff is scoped exactly to its stated defect (2 stems dropped, nothing else
in the pattern moves), its safety claim for the remaining 19 stems is now backed by a
probe that checks the property per member and re-derives its inputs from the live
regex rather than restating them, and every probe in the negation-handling lineage
(2023, 2025, 2028, 2031, plus the unit test suite) is green against the shipped code.
**CONFIRM.**
