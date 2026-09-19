# wq#2023 — verify-pass on wq#2022 / PR #644 ("clause-scope route_conflict, reject negated adds")

**Verdict: CONTRADICT.** The shipped fix narrows the false-positive window but does
not close it: `_NEGATION_RE` is a hardcoded enumeration of negation spellings, not a
negation detector, and it omits the contracted modal auxiliaries ("shouldn't",
"wouldn't", "couldn't", "can't", "mustn't"). Any disclaimer proposal phrased with one
of those still hard-rejects as a duplicate route — the exact bug class wq#2019 /
wq#2021 / wq#2022 all targeted, reproduced live against the shipped module with a
different negation word.

## What PR #644 claims

`ops/wq2019-proposal-reviewer-fix/minerva_dedup.py:132-191` clause-scopes the
±60-char proximity window (`_CLAUSE_SPLIT_RE`) and adds `_NEGATION_RE` so a
proposal that explicitly disclaims a route add ("this doesn't add a new model...")
no longer trips `route_conflict()`. `test_route_conflict.py` covers four disclaimer
phrasings (`doesn't`, `does not`, `rather than`, curly-apostrophe `doesn't`) plus two
negation-adjacent true positives, all passing.

## Break attempt

Read `_NEGATION_RE` cold:

```
r"\b(not|no|never|doesn'?t|does not|don'?t|do not|isn'?t|is not|aren'?t|"
r"won'?t|will not|without|rather than|instead of)\b"
```

This is an explicit word list, not `n't`-aware. English has more modal-negation
contractions than the four verbs enumerated (`do/does/is/are/will` + `not`).
Swapped the modal in wq#2022's own passing test case and ran it against the shipped
module, same config/route list the test suite uses:

```
$ python3 probe_negation.py
(True, 'bge-m3') | Wrap the gateway in a cache; this shouldn't add a new litellm route for bge-m3.
(True, 'bge-m3') | Wrap the gateway in a cache; this wouldn't add a new litellm route for bge-m3.
(True, 'bge-m3') | Wrap the gateway in a cache; this can't add a new litellm route for bge-m3.
(True, 'bge-m3') | Wrap the gateway in a cache; this couldn't add a new litellm route for bge-m3.
(True, 'bge-m3') | Wrap the gateway in a cache; this mustn't add a new litellm route for bge-m3.
```

All five return `(True, 'bge-m3')` — a hard reject — despite being the identical
disclaimer shape as the passing `doesn't` case one line above it in the test file.
Confirmed none of `shouldn/wouldn/couldn/mustn/can't/cannot/needn` appear anywhere
in `minerva_dedup.py` or `test_route_conflict.py` (`grep`, zero hits) — this isn't a
narrow miss, the whole contraction family is absent.

Mechanism: for the pre-verb check, `_NEGATION_RE.search(low, lo, m.start())` only
fires if one of the listed tokens is literally present. `shouldn't`/`wouldn't`/
`couldn't`/`can't`/`mustn't` don't decompose into any listed alternative (they're
not `do/does/is/are/will` + `not`), so the search comes back empty and the clause is
treated as an un-negated add — same terminal path as the original wq#2019/wq#2021
bug: `minerva_proposal.py:482-487` writes a hard `Duplicate: LiteLLM route "..."`
`decision_reason` and the proposal never reaches `check()` or the judge.

## Why this matters for the verdict

wq#2022's own report frames the fix as "a negation token anywhere inside that span
... kills the match" — stated as a general property, not "these four negation
forms." The shipped enumeration doesn't deliver that property; it delivers
coverage for the specific spellings wq#2021's one repro happened to use
(`doesn't`, curly `doesn't`, `does not`, `rather than`). A generated proposal is as
likely to hedge with "shouldn't" or "wouldn't" as with "doesn't" — nothing in the
proposal-generation path favors one modal over another — so this isn't a
theoretical edge case, it's the same bug surviving under a different word choice.

## Not in scope / not reproduced as new findings

- `_ADD_INTENT_RE` also only matches base/`-ing` forms (`add`, `adding`, not
  `added`/`adds`) — a passive-voice genuine duplicate ("bge-m3 should be added as a
  new route") is a false *negative* today. This predates PR #644 (unchanged in the
  diff) and is a separate, pre-existing gap — not part of what this PR claims to
  fix, so not raised as a contradiction of *this* diff, but worth a follow-up
  ticket.
- Did not re-run `install.sh`'s health assertions; PR #644's own report already
  covers that path and this finding doesn't touch it.

## Reproduction

`reports/2023_probe_negation.py` imports `minerva_dedup` from
`ops/wq2019-proposal-reviewer-fix/`, builds the same tempfile route config
(`glm-5.3-flash-local`, `qwen36-local`, `bge-m3`, `claude-max-sonnet`) the shipped
test suite uses, and calls `route_conflict()` directly on the five modal-swap
strings above — output pasted verbatim in "Break attempt."

Not implementing a fix here — this is a verify-pass, not a re-implementation. Leaving
the fix shape (likely a general `n't`-contraction pattern rather than a longer
enumeration, plus a probe pass to confirm it doesn't over-match) to the escalated
judgment.
