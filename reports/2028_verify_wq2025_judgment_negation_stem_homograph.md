# wq#2028 — verify-pass on wq#2025 / PR #646 ("contraction-aware negation in route_conflict")

**Verdict: CONTRADICT.** PR #646 replaces the enumerated-auxiliary negation list with
a general `\w+n't` contraction pattern plus a short list of bare `<stem>nt` spellings
for auxiliaries whose "unambiguous" bare-`nt` form the PR's own comment claims is safe.
That claim is false: two of the enumerated stems (`ca`+`nt` = "cant", `wo`+`nt` =
"wont") are real English words. When one appears as an ordinary word in the same
clause as a genuine route-add proposal, `_NEGATION_RE` misreads it as a negation and
`route_conflict()` silently clears a real duplicate route add — the exact failure
mode the PR's own comment calls out as worse than the bug it was fixing ("a general
`\w+n'?t` swallows ordinary words ... and would suppress REAL conflicts, a worse
failure than the one being fixed"). The PR avoided that risk for the *general* arm by
requiring an apostrophe, then reintroduced it via the *enumerated* bare-`nt` arm it
added right next to that reasoning.

## What PR #646 claims

`ops/wq2019-proposal-reviewer-fix/minerva_dedup.py:160-165` replaces the wq#2022
enumerated auxiliary list with:

```python
_NEGATION_RE = re.compile(
    r"\b(?:\w+n't|"
    r"(?:do|does|did|is|are|was|were|wo|ca|sha|has|have|had|could|would|"
    r"should|must|might|need|ought|ai)nt|"
    r"not|cannot|no|never|without|rather than|instead of)\b"
)
```

The commit message and inline comment (lines 146-159) frame this as closing wq#2023's
modal-contraction gap while deliberately avoiding a naive `\w+n'?t` (apostrophe
optional) generalization, because that swallows ordinary words like "persistent" and
"different" and would suppress real conflicts — measured against
`reports/2025_probe_negation_contraction.py`, which reports 0 false positives / 0
false negatives for the shipped (CHOSEN) pattern. `test_route_conflict.py` gained 9
new disclaimer cases (shouldn't/wouldn't/can't/couldn't/mustn't/mightn't/needn't/
shan't/cant/cannot) and 2 new true-positive cases using "persistent" and "different"
as decoy `nt`-ending words. All pass.

## Break attempt

The PR's stated safety argument is: the general `\w+n't` arm requires an apostrophe
because bare `nt` is ambiguous with ordinary words, but the *enumerated* stems
(`do/does/did/is/are/was/were/wo/ca/sha/has/have/had/could/would/should/must/might/
need/ought/ai` + `nt`) are safe to spell without an apostrophe because their bare
form is "unambiguous." Checked each stem+`nt` concatenation against actual English:

- `ca`+`nt` = **"cant"** — a real noun/adjective (insincere jargon; a slanted/tilted
  surface, as in "roof cant" or "bank cant" in engineering/rail contexts). Not in the
  OLD (pre-#646) `_NEGATION_RE` at all — this collision is newly introduced by this
  diff, since `can't`/`cant`/`cannot` were entirely absent before (that absence was
  wq#2023's finding).
- `wo`+`nt` = **"wont"** — a real noun/adjective (customary habit, "as is her wont").
  This one isn't new to #646: the pre-#646 pattern already had `won'?t` (apostrophe
  optional), so this exact collision predates this PR, but PR #646's own comment now
  explicitly asserts the *general* class of bare-`nt` stems is safe precisely because
  it's "unambiguous" — an argument the shipped code doesn't actually verify per-stem,
  and which is false for this stem it inherited unmodified.

Built two proposal strings that are genuine route-add duplicates for `bge-m3` (an
existing route in the test config), with the verb, the homograph word, and the route
name all in the same clause so no clause-scoping is at play — same test harness
(`minerva_dedup.LITELLM_CONFIG` pointed at a tempfile with the same four routes the
shipped suite uses):

```
$ python3 reports/2028_probe_negation_stem_homograph.py
cant -> (False, None) | Add a cant-adjusted route named bge-m3 to the litellm model pool as a new entry.
wont -> (False, None) | Register the wont pattern as a new litellm route named bge-m3 in the model pool.
```

Both are unambiguous route-add proposals for a route name that already exists in the
config — `route_conflict()` should return `(True, 'bge-m3')`, same as the shipped
suite's existing `persistent`/`different` true-positive cases. Instead both return
`(False, None)`: the proposal is silently waved through as not-a-duplicate, and per
`minerva_proposal.py`'s dedup gate a proposal to re-add an existing route with no
downstream catch would go on to actually register a duplicate `model_list` entry —
this is a false *negative* on the gate's core job (catch real duplicate route adds),
not the false-positive class the wq#2019→wq#2025 chain was built to fix.

## Why this matters for the verdict

This isn't the same bug recurring under a new spelling (the wq#2019→wq#2023 pattern);
it's the opposite-direction failure the PR's own reasoning identifies as strictly
worse, reproduced against the enumerated arm the PR added to avoid it in the general
arm. The PR's probe (`reports/2025_probe_negation_contraction.py`) only exercises
"persistent" and "different" as decoy `nt`-words — both miss the general `\w+n't` arm
by construction (no apostrophe) but were never checked against the *enumerated*
stems, so the probe's "0 false negatives" result doesn't cover the failure mode
demonstrated here. `cant`/`wont` never appear in `minerva_dedup.py` or
`test_route_conflict.py` as positive-case decoy words (`grep`, zero hits) — this
wasn't tested, not narrowly missed.

## Not in scope / not reproduced as new findings

- Also probed non-curly apostrophe substitutes (acute accent `´`, modifier-letter
  apostrophe `ʼ`, backtick, wrong-direction curly quote `'`, prime mark `′`) in
  `doesn't`-style disclaimers — all five still hard-reject as a duplicate, since only
  the one curly quote (`'` U+2019) the PR's own test comment cites as "what
  LLM-written proposal text actually emits" is normalized before matching. Weaker
  evidence than the homograph finding above (these characters are not typical LLM
  contraction punctuation, unlike U+2019), so not raised as the primary contradiction,
  but worth folding into the same fix pass since the normalization is a one-line
  extension (`.replace()` chain or a small translation table).
- Did not re-check `_ADD_INTENT_RE`'s base/`-ing`-only verb-form gap flagged as
  pre-existing and out-of-scope by wq#2023 — still unchanged by this diff.

## Reproduction

`reports/2028_probe_negation_stem_homograph.py` imports `minerva_dedup` the same way
`reports/2023_probe_negation.py` and `reports/2025_probe_negation_contraction.py` do,
builds the same tempfile route config, and calls `route_conflict()` directly on the
two strings above — output pasted verbatim in "Break attempt."

Not implementing a fix here — this is a verify-pass, not a re-implementation. Fix
shape for the escalated judgment: check each enumerated bare-`nt` stem against a
dictionary/wordlist (or just drop the bare-`nt` spellings for stems that collide with
real words and rely on `not`/`cannot` plus the apostrophe'd form instead — the
apostrophe'd spelling is overwhelmingly how these auxiliaries actually appear in
prose, uncontracted "<aux> not" is already covered by bare "not"), then re-run both
`test_route_conflict.py` and this probe before merge.
