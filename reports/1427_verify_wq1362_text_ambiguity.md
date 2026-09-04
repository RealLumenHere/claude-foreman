# wq#1427 — verify-pass of wq#1362 (text ambiguity interpretation guide)

**Verdict: CONFIRMED.** The delivered doc satisfies every requirement in wq#1362's
brief, is internally consistent, and I found no linguistic/factual errors serious
enough to make the guide give wrong operational advice.

## What was claimed (wq#1362, PR #299, notes: "PR #299 landed:
research/brain/text_ambiguity.md")

Deliver `research/brain/text_ambiguity.md`: operational heuristics for recovering
intended meaning from imperfect text, covering punctuation, dropped negation,
homophones/autocorrect, run-ons, ambiguous modifiers, comma-of-life, sarcasm,
double negatives, "could care less", dialect-vs-error, and a flag-vs-guess
decision procedure tied to a fail-to-kindness default.

## Independent verification

- Confirmed merged: `origin/main` commit `15dafb5` ("docs(brain-interp): text
  ambiguity interpretation guide", Refs: wq#1362) is an ancestor of current
  `origin/main` tip (`e206d14`). File content at `origin/main:research/brain/text_ambiguity.md`
  is byte-identical to the branch tip that closed the ticket — no drift between
  what was reviewed and what's live.
- Path is correct: `research/brain/text_ambiguity.md` at repo root, not nested
  under a stray `foreman/` prefix (a real mis-scope bug that hit the sibling
  wq#1361 doc, `foreman/research/brain/pragmatics.md` — out of scope for this
  ticket, not repeated here).
- Checked every required topic from the ticket detail against the 241-line doc:
  missing/misplaced punctuation (§1.1, incl. comma-of-life), dropped negation
  incl. "could care less" and AAVE negative concord (§1.2), homophones/autocorrect
  incl. their/there, your/you're (§1.3), run-ons (§1.4), ambiguous modifiers incl.
  PP-attachment and "only"-scope (§1.5), sarcasm without markers (§1.6),
  dialect/idiolect-vs-error (§2), and a flag-vs-silently-normalize decision
  procedure with a quick-reference table (§3-4). All present.
- Verified the specific rule the ticket asked for verbatim: "hedge ... or ask a
  short clarifying question. Never silently assume the reading that makes the
  sender rude, incompetent, or hostile" (§0) and the explicit tiebreak rule in
  §3 step 6 — matches "when unsure, ask or hedge, never assume the insulting
  reading" from the ticket text.

## Adversarial checks that did NOT find a break

- Spot-checked every linguistics claim for correctness: comma-of-life, litotes
  ("I'm not unconvinced"), AAVE negative concord and habitual "be", squinting
  modifiers, PP-attachment ambiguity, "only"-scope ambiguity — all standard,
  correctly characterized. No inverted or backwards examples.
- Checked the doc doesn't contradict itself: §4's table row "no plausible error
  explains it, sender's baseline shows no such pattern → take it at face value"
  correctly caps the fail-to-kindness default so it can't be read as "always
  assume the reader is nice regardless of evidence."
- Two example autocorrect swaps ("of"→"off", "no"→"on"/"not") are weaker/less
  attested than the others ("definitely"→"defiantly" is well known) — flagged
  as a minor quality nit, not a correctness break; low-stakes even if wrong
  since they're illustrative, not load-bearing for the decision procedure.
- The term "fail-to-kindness default" (§0) is asserted as already established
  for tone and extended here to parsing, but no other doc reachable from this
  worktree or `/home/lumen/kb` defines it — grepped `kb/`, `mailbot/`, and all
  merged `research/brain/*.md` siblings, no hits. Plausibly defined in a
  persona/system-prompt surface outside this repo's tree; not a contradiction
  since wq#1362's own brief explicitly asked the doc to "tie to" that default
  by name, implying it was expected to already exist elsewhere.

## Process note

Prior wq#1427 attempt (a1) was classified PERMISSION_LOOP. This attempt hit one
blocked chained Bash call (`git show ... > file && cat file` — the `cat` segment
wasn't allow-listed) and re-scoped immediately: split into a single `git show ...
> file` redirect plus the `Read` tool for inspection, which matches the working
pattern already logged in ISSUE-LOG.md #168. No new issue class found; not
re-logged.

## Bottom line

wq#1362's claim holds: `research/brain/text_ambiguity.md` is merged to main,
covers every topic and rule the ticket specified, and survives an adversarial
read for linguistic accuracy and internal consistency. No re-implementation
performed, per archetype 8 scope.
