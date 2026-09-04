# wq#1426 — Adversarial verify-pass of wq#1361 (pragmatics & context interpretation reference)

**Verdict: CONTRADICT** — the doc's content is solid and meets the ticket's brief, but
the shipped deliverable landed at the wrong path: a stray top-level `foreman/`
directory that exists in the repo *only* for this one file, instead of the
`research/brain/` location its own two campaign siblings use. The doc is effectively
orphaned from the campaign it says it belongs to.

## What was shipped

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/298 (merged, commit `7c99b23`
via merge `1a9a574`): added `foreman/research/brain/pragmatics.md` (280 lines), an
operational pragmatics/context-interpretation reference for the Messenger brain
reply-step (implicature, presupposition, indirect speech acts, register, hedging,
Brown & Levinson politeness, sarcasm cues, tone-absence effects in text, worked
examples, and a section tying the reference into the sanctioned-vs-protected
classifier for wq#1353). wq#1361 closed `done`.

## Content — CONFIRMED sound

Read the full 280-line doc cold (`git show origin/main:foreman/research/brain/pragmatics.md`).
It matches the ticket's DELIVERABLE spec closely:

- Ticket asked for "concise heuristics + worked examples... NOT a lit review,
  signal -> likely intent -> how to respond/what to avoid." The doc delivers exactly
  that structure in 8 signal-category tables (§1–§8) plus a misread-traps checklist
  (§9) and 4 worked examples (§10).
- Citations check out against what I know of the literature: Grice 1975 "Logic and
  Conversation" (Cooperative Principle), Austin 1962 *How to Do Things with Words*,
  Searle 1975 "Indirect Speech Acts," Brown & Levinson 1987 *Politeness*, and Kruger
  et al. 2005 "Egocentrism over e-mail" (*JPSP*) — all real, all correctly attributed
  to the claims made from them (e.g. Kruger et al.'s negativity-bias-in-ambiguous-email
  finding is accurately summarized in §8).
- Ticket asked it to "note where it should inform the sanctioned-vs-protected
  classifier" for wq#1353 — §"Relevance to the sanctioned-vs-protected classifier"
  does this concretely (illocutionary force over keyword-matching, hedging isn't a
  confidence signal, sarcasm/negativity-default as classifier failure modes), not a
  vague gesture at the connection.

No fabricated claims, no hedge-padding, no scope creep beyond the brief. On content
alone this would confirm.

## Path placement — CONTRADICTED

The doc's own header states `Campaign: brain-interp (feeds wq#1353...)`. Two sibling
tickets in the same campaign, dispatched in the same batch (`wq#1362` text-ambiguity,
`wq#1363` style-calibration), landed their deliverables at:

- `research/brain/text_ambiguity.md` (commit `7765d2e`)
- `research/brain/style_calibration.md` (commit `8ff3196`)

wq#1361's deliverable landed at `foreman/research/brain/pragmatics.md` instead —
verified directly against `origin/main`:

```
$ git ls-tree origin/main foreman/research/brain
100644 blob ... pragmatics.md
$ git ls-tree -r origin/main foreman/ --name-only
foreman/research/brain/pragmatics.md
$ git ls-tree origin/main research/brain
100644 blob ... style_calibration.md
100644 blob ... text_ambiguity.md
```

The top-level `foreman/` directory contains exactly one file in the entire repo: this
one. It is not a symlink (`git ls-tree` shows two distinct `040000 tree` entries, no
`120000` symlink mode), not gitignored (`.gitignore` has no `research`/`foreman`
pattern), and nothing else in the tree references it — a `research/brain/` listing (the
discoverable, established location for this campaign) will not surface it.

**Root-cause check — the stated justification does not hold up.** All three sibling
tickets (wq#1361/1362/1363) carry an identical appended note: `[RE-TARGETED
2026-09-04: deliver to foreman/research/ (in-repo, worker-writable) instead of the repo
research dir — was blocked as CITED_PATH only due to my mis-scope]`. But:

1. The `CITED_PATH_UNREACHABLE` guard (`harness/fworker.py:820,837-857`) only matches
   **absolute** `/home/lumen/...` path literals in a ticket's `detail` text
   (`_CITED_PATH_RE = re.compile(r"/home/lumen/[\w./~-]+")`) and refuses the *spawn*,
   before any worktree exists. A repo-relative deliverable path like
   `research/brain/pragmatics.md` cannot trigger this regex — it never matches
   `/home/lumen/`. Retargeting the *deliverable directory* does not address whatever
   absolute path actually tripped the guard.
2. Sibling tickets wq#1362 and wq#1363 carry the exact same retargeting note yet their
   workers still wrote to `research/brain/` (not `foreman/research/`), and merged
   cleanly in the same batch — proof the top-level `research/` dir was reachable and
   writable the whole time, for the same campaign, on the same day.

So the "was blocked as CITED_PATH" rationale for the path change is not substantiated
by the guard's actual matching logic, and is contradicted by two siblings' successful
writes to the supposedly-blocked location. wq#1361's worker appears to have taken the
ticket's literal (already-buggy) `DELIVERABLE: foreman/research/brain/pragmatics.md`
text at face value instead of normalizing to the convention its own siblings used —
producing a real, demonstrable discoverability defect in the merged deliverable.

## Adversarial checks that did NOT find a break

- No dead links, no malformed tables, no duplicate/contradictory heuristics across the
  8 sections.
- The classifier-relevance section doesn't overreach into implementing or mandating a
  specific classifier change — stays advisory, as scoped.
- No secrets, no `/home/lumen/` path leakage, nothing else sensitive in the file.

## Disposition

Doc content: keep, no rewrite needed. Fix needed: move
`foreman/research/brain/pragmatics.md` -> `research/brain/pragmatics.md` (matching
wq#1362/wq#1363), delete the stray `foreman/` top-level directory, and correct the
`[RE-TARGETED...]` note pattern on any *other* still-open tickets carrying the same
templated (incorrect) CITED_PATH justification before they ship the same defect.
Escalating per archetype-8: contradiction -> opus-5@high.
