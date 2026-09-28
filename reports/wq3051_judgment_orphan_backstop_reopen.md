# wq#3051 [judgment] — the orphan backstop's trailing run must not re-open on a prefix

**Decision: tighten with a third condition, in both copies. No revert.** wq#3037's
CONTRADICT of wq#3018 is upheld on every point, including the one it did not need to
argue: the over-fire is not hypothetical, it has already cost a rotation row (ISSUE-LOG
1001 / wq#3040). `e2dbe416` stays on `main` — it closes the under-floor leak wq#2922
filed, and nothing here touches `spans`, so no text was ever mis-redacted.

## What was wrong

`masking._orphan_matches()` asked two questions of a prefix whose guarded body run
stopped on the mask literal below its rule's floor: is our own output sitting there
(`_OWN_TAIL`), and would the two halves either side of it have reached the floor. The
docstring (`masking.py:697-699`, pre-fix) claimed a third was unnecessary because a
second same-family prefix behind the literal is something "this rule's charset cannot
run through". That is false for five of the six `_ORPHAN_SHAPES` rows — a prefix's own
characters are a subset of its own body charset by construction, and only github's
`[A-Za-z0-9]` excludes anything (the `_` in `ghp_`). So the trailing run walked into the
second mention and lent its length to the first, and
`<prefix><under-floor fill>MASKED<prefix>` alarmed on text with no credential in it.

## Why it mattered rather than merely being untidy

`token_engine_daemon._orphan_matches` is the mirrored copy `read_worktree_file`
(`token-engine/token_engine_daemon.py:692-715`) reads a PR title and body through, and
one hit there is `Deny("body_mask_tripwire", ...)` — the `lumen-pr` path every worker
lands on. And the shape it fires on is what a report about masking looks like. That is
not a prediction: `harness/tests/fixtures/wq3037_values.py:1-21` pins three values a
real worker run recorded as BURNED from exactly this chain, and ISSUE-LOG row 1001
records wq#3040 opening and closing a rotation row over one of them with the verdict
"No rotation."

## The decision, and the argument that carries it

Third condition: **if a known fixed prefix starts exactly where our own output ended,
that is a new mention, not this body resuming, and its characters are not this match's
to count.**

```python
if _PRE_ANY.match(text, tail.end()):
    continue  # a second mention opens there — not this body resuming
```

The POSITION is the whole argument, and it is what makes this a tightening rather than a
guess:

- A prefix sitting *there* is preceded by the mask literal — the one place `_B`'s
  `_AFTER_MASK` lookbehind admits a prefix glued to what is in front of it. So that
  second mention is **already read as a match in its own right**, by this same loop and
  by `PATTERNS`. Counting it here counted it twice.
- A prefix appearing *later* inside the trailing run is glued to body characters, `_B`
  refuses it, and nothing else will read it. The run keeps it — that is the wq#2702 glue
  `read()`'s phase 2 exists for, and it stays body. This is why the fix is a
  position-anchored `match()` and not "truncate the run at the first prefix", which
  would have thrown that coverage away.

`_PRE_ANY`, not the rule's own prefix: the over-fire is cross-family too
(`cfut_` + fill + `MASKED` + `ghp_` fires identically, since every charset here but
slack's admits AKIA and cloudflare's admits all of them), and the same position argument
covers any of the six.

**What it gives up**, stated plainly: a live body that contains `MASKED` and then,
immediately behind it, one of the six prefixes, with too few characters after that
prefix to reach its own floor. Rarer than the 1-in-62\*\*6 the literal already costs,
and bought against a false `Deny` reachable on the landing path today. The other option
on the table — a narrower trailing charset — was rejected: every prefix but `AKIA` ends
in `_` or `-`, so narrowing the charset to exclude them mutilates real bodies (`sk-`'s
own base64url alphabet carries both) to reach the same place less precisely.

## Evidence

`reports/wq3051_probe_orphan_prefix_reopen.py`, five sections pulling in opposite
directions. It takes the wq#2868 argv convention, so the before/after is one artifact:

```
$ python3 reports/wq3051_probe_orphan_prefix_reopen.py /tmp/.../prefix   # masking.py @fe123068
1 reopen: no-credential text must not alarm: 20 cases, 20 failing
2 under-floor via MASK guard (SILENT = fail): 96 cases, 0 failing
5 second link of the chain still fires and is redacted: 6 cases, 0 failing
3b under-floor [daemon mirror] (SILENT = fail): 96 cases, 0 failing
4 quiet on committed text outside wq#3018's sweep @fe123068: 7 cases, 0 failing
TOTAL FAILING 20

$ python3 reports/wq3051_probe_orphan_prefix_reopen.py                   # this branch
1 reopen: 20 cases, 0 failing        2 under-floor: 96 cases, 0 failing
5 second link: 6 cases, 0 failing    3a reopen [daemon]: 20 cases, 0 failing
3b under-floor [daemon]: 96 cases, 0 failing   4 outside @fe123068: 7 cases, 0 failing
TOTAL FAILING 0
```

(`3a` reads 0 under the argv path because argv only redirects `masking`; the daemon copy
is always the working tree's. The pre-fix daemon number is the 20/20 in the run against
the unmodified worktree, captured before the edit.)

Why each section can fail, since wq#2630 is the reason this ticket exists:

- **1 reopen** is built from outside the population the fix was designed against, and
  fails 20/20 on the code wq#3037 measured. Every case first asserts `mask_text()`
  returned its input byte-for-byte, so a hit is attributable to the backstop and to
  nothing in `PATTERNS`.
- **2 / 3b** are wq#2868 section B unchanged, the 96 real under-floor leaks. A
  tightening that bought section 1 by going silent here would have re-opened wq#2922;
  these are the cases that would have caught that.
- **5 second link** is the falsifier for the fix's own argument, not a confirmation of
  it: if a prefix behind the literal were *not* already read as its own match, the
  second link would go silent or unredacted here.
- **4 outside** is rev-pinned (`git show fe123068:<path>`) over six committed files that
  are **not** in wq#3018's 14-file `quiet()` list, including wq#3037's own report — the
  document whose subject matter is the adjacency this rule was mis-reading.

Tests: `harness/tests/test_mask_underfloor_invariant.py` gains four cases wrapping
sections 1, 3a, 5 and 4, plus the two-copy silence check.
`test_mask_underfloor_invariant.py` + `test_mask_mirror_coverage.py`: 61 passed. The
seven-file mask suite: **1243 passed, 1 failed**, and the failure is pre-existing and
unrelated — see below.

## One thing I did not fix, on purpose

`test_masking.py::test_the_mask_suite_does_not_arm_its_own_tripwire` fails on this
branch *and* on `fe123068`:
`reports/wq3021_verify_wq2924_sk_key_own_prefix_gap.md` (committed in `51045dd8`, weeks
before any of this) carries a whole `sk-key` shape, so merely reading the repo files a
rotation row. Measured both ways with the pre-fix and post-fix modules against that one
file: identical `[('sk-key', 'sk-ant-', 20)]`, and `_orphan_matches()` returns `[]` on it
in both — it is an ordinary `PATTERNS` match and has nothing to do with this backstop.
Left alone because fixing it means editing another verify-pass's committed evidence text,
and split at the wrong seam that edit changes what a pinned citation says. Logged as
ISSUE-LOG 1003 for a ticket of its own.
