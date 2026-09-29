# wq#3131 — verify pass on wq#3129 (pilot-lane card-lint, PR#1407, 5aaf639d31)

**Verdict: CONFIRM.** The fix closes the exact gap wq#3126 reported; independently
re-verified against the real corpus, not just the author's claim.

## What was claimed (wq#3129 / ISSUE-LOG row 1047)
- `COMMENT_LINE` (`re.match`-anchored at line start) only skipped a line whose
  ENTIRE stripped text was a `#` comment, so a trailing comment on a real command
  line (`python3 run_pilot.py  # see /api/v1/users for the schema`) still had its
  prose scanned by `_PATH_TOKEN`, producing a phantom "does not resolve" flag.
- Fix (`harness/pilot_spec_author.py:379,412`): `COMMENT_SPAN = re.compile(r'(?<!\S)#.*')`
  strips the comment as a *span* (like `URL_SPAN` strips a URL) rather than
  requiring the whole line to be a comment. The `(?<!\S)` lookbehind means `#` must
  start a word (preceded by whitespace or line-start) to be treated as a comment
  marker, so a ticket ref (`wq#3129`) or a URL fragment (`page#frag`) keeps its `#`.
  Author's commit message states a grep of the 52 committed cards found no other
  "attached-`#`" form beyond those two.

## Independent verification (not just re-running the author's tests)
1. `python3 -m pytest harness/tests/test_pilot_spec_author.py -q` → 21/21 pass,
   including the 4 new probes (wq#3126's verbatim `lint_card()` repro, a phantom
   path beside a trailing comment, word-internal `#` preservation for ticket refs
   and URL fragments, whole-line comment still skipped).
2. Re-ran `lint_card()` against all 52 committed `pilots/*/CARD.md` files under
   **both** the pre-fix module (`git show 98e08381:harness/pilot_spec_author.py`,
   the parent of the fix commit) and the post-fix module, loaded side-by-side via
   `importlib`, and diffed the JSON results by path: **byte-identical** — no card's
   lint verdict changed. This independently confirms the author's "all 52 cards
   lint identically pre/post" claim rather than trusting it.
3. Independently grepped every committed card's Baseline/Treatment section body
   (not just command-shaped lines) for an attached `#` (`\S#`): 4 hits total, all
   ticket-ref shapes (`` wq#957 ``, `` `wq#N` ``, `` wq#1560 ``, `` wq#1903 ``) —
   confirms the author's corpus grep was accurate and complete, not just asserted.
4. Confirmed the originally-cited real card, `pilots/0eed2f5bdb92/CARD.md:81-82`
   (the trailing-comment shape wq#3126 pointed at), lints to `[]` on both sides —
   it was cited only as evidence the *shape* is in real use, not that this
   specific card was broken (its paths are real files from a completed pilot).

## Adversarial probes beyond the shipped test population
Tried to find an input from outside the fix's own tested population that still
produces a wrong `lint_card`/`_referenced_paths` result:
- Multiple `#` on one line mixing a ticket ref and a real trailing comment
  (`--ticket wq#3129  # see /pilots/missing/x.json`) → correctly strips only the
  real comment, keeps the command path. Works because the regex engine only
  attempts a match where a literal `#` exists, and the leftmost `#` that satisfies
  `(?<!\S)` is the real comment marker.
- Tab before `#` → whitespace, strips correctly.
- **Found a real (but out-of-corpus) gap**: when `#` is immediately preceded by a
  non-whitespace character that isn't part of a word-ref shape — e.g. a comma or
  closing paren with no space (`run.py,# see /pilots/missing/x.json`,
  `run.py)# see /pilots/missing/x.json`) — `(?<!\S)` fails (the preceding char is
  `\S`), so the span is *not* stripped, and `/pilots/missing/x.json` in the
  "comment" prose still gets read as a referenced path. If that path doesn't
  resolve, `lint_card` would wrongly reject a valid card. Verified via
  `_referenced_paths` directly (probe script kept at
  `/tmp/fworker-wq3131/probe1.py` in this ticket's worktree environment).

## Why this does not rise to a contradiction
- It does not occur anywhere in the 52-card corpus (step 3 above) — unlike
  wq#3121's and wq#3126's contradictions, which each reproduced against a real,
  already-committed card outside the fix's own test population. This is a
  synthetic construction, not a demonstrated live defect.
- The natural, conventional way to write a trailing shell comment always has a
  space before `#` (`foo, # comment`, `foo) # comment`), which is already handled
  correctly — only a no-space-before-`#` authoring style breaks it, and that style
  is not used anywhere in this repo.
- wq#3129's own ticket text explicitly scoped the fix to "careful not to treat a
  literal '#' inside a real path/argument as a comment marker if one exists in
  **this corpus** (grep first)" — the author did grep, found none, and disclosed
  the exact boundary in the commit message. This is a known, disclosed, verified
  out-of-scope edge, not a hidden regression.

Logged for the record in case a future card is authored with a no-space
attached-`#` trailing comment; not filing a follow-up fix ticket since there is no
live instance and the fix already satisfies its own stated acceptance bar.

## Evidence
- `python3 -m pytest harness/tests/test_pilot_spec_author.py -q` → `21 passed`
- Pre/post corpus diff: `/tmp/fworker-wq3131/pre.json` vs `/tmp/fworker-wq3131/post.json`
  (identical, `diff` exits 0)
- Adversarial probes: `/tmp/fworker-wq3131/probe1.py`, `probe2.py`
