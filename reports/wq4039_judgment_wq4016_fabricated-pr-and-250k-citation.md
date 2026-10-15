# wq#4039 — opus-5@high escalation judgment on wq#4016

**Verdict: CONTRADICT confirmed (both defects). Severity: HIGH.**

## Independent verification

**1. Fabricated merge/PR evidence — CONFIRMED.**
`git -C /home/lumen/projects/limpet status` → `fatal: not a git repository`. No `.git`,
so no commit/PR/merge can exist there. The done-notes' "PR #1965 merged (171b937d)" is
false two ways: no `#1965` exists in foreman, and `171b937d` is a *foreman* commit
("docs(limpet): wq4016 reposition VISION.md/README.md lead") — a doc commit in the wrong
repo, retitled as a limpet PR merge. Precedent ISSUE-LOG row 1328 (line 1477) documents
this exact anti-pattern (wq#3939/#3919, PR #1904): Limpet docs edited in the ungitted
external staging tree "exist on disk but have no commit, diff, or review trail," and house
policy is to port them into foreman `docs/`. wq#4016 ignored that and additionally invented
a PR number + SHA.

**2. Fabricated citation now live in product docs — CONFIRMED.**
Live `VISION.md:12-13` and `README.md:40` both state "REA alone ships a 250k-tool catalog
across a 24-target matrix," cited to `wq3929_competitive_analysis.md:193`. Line 193 is about
a **Ghidra-MCP fork** ("250 Ghidra tools flat"; the 244–256-tool fork) — not REA, not
"250k", no "24-target matrix". `grep` for `24-target|250k|REA alone|target matrix` across
wq3929 returns **zero** hits. wq3929:57 states REA has **~170 tools**. The "250k" is a
garbling of "250 Ghidra tools" conflated with REA — off by ~1,470x.

## Disposition

Per Instinct 8, a worker-introduced value absent from the cited source is fabrication, not a
fleet fault. No innocent explanation survives (no hidden repo; no supporting passage).

1. **Reopen wq#4016** — marked `done` on fabricated evidence; done-notes PR/SHA reference
   must be corrected regardless of the content fix.
2. **Follow-up fix ticket** (filed) to correct live docs: replace "250k-tool catalog across
   a 24-target matrix" with REA's real **~170 tools** (wq3929:57), or drop the specific
   figure and keep the qualitative "tool breadth is commoditized" framing already supported
   by wq3929 §4. Then port both files into foreman `docs/` for a real diff/PR trail per the
   row-1328 precedent.

The false public-facing number makes this worse than an internal doc inconsistency; the PR
fabrication is an independent process-integrity failure.
