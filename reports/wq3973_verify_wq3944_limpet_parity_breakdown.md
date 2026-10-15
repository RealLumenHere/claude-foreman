# wq#3973 verify-pass of wq#3944 — LIMPET competitive-parity adopt-list breakdown

PR #1917 (merged, squash-commit `1651c9bb`, branch-tip `6499b044`).
Author model: claude-opus-5-5. Verifier: claude-sonnet-5@high (fresh instance).

## What shipped

wq#3944 was a placeholder ticket ("implement missing features") blocked on wq#3929's
competitive analysis. Its actual deliverable, once unblocked, was **not code** — it is a
pure-docs commit adding `design/wq3944_parity_breakdown.md` (45 lines): a table mapping
each of wq#3929's 11 adopt items (A1–A11) to a disposition (filed / folded / declined),
plus a `Decisions` section justifying the ordering, the A9 fold, and the A11 decline. The
ticket's own notes claim: filed wq#3964–3972, folded A9 into wq#3945, declined A11, and
flagged search-down (ISSUE-LOG #1325) as the reason A3/A5/A6 get a prior-art re-verify
step instead of a hard block.

## Checks performed

1. **Source doc cross-check** — read `design/wq3929_competitive_analysis.md` in full (315
   lines: gap list G1–G8, adopt list A1–A11 ranked by value/cost in §5). Every A1–A11 item
   appears in the breakdown table exactly once; every G1–G8 gap is referenced by the
   correct adopt item (G1→A2, G2→A1, G3→A3, G4→A11, G5→A8, G6→A10, G7→A7, G8→A9). No
   adopt item dropped, none duplicated.
2. **Sub-ticket existence and fidelity** — `wq show` on all nine filed tickets
   (3964–3972): title, `pri`, and `deps` match the breakdown table's row for each,
   cell-for-cell (e.g. wq#3966: pri 2, deps=3965, matching "A3 | p2 | wq#3965"; wq#3971:
   pri 3, deps=3964, matching "A8 | p3 | wq#3964"). Spot-checked full `detail` text on
   wq#3968 (A5): it carries the design hook (`design/wq3806_limpet_architecture.md:233-239`),
   a concrete AC (manifest schema field + router unit test), and the promised prior-art
   re-verify-first step for a *(described)* platform — matching the breakdown doc's
   "Decisions" §4 claim that A3/A5/A6 each begin with a re-verify step rather than a block.
3. **A9 fold claim** — `wq show 3945`: item (3) of its scope is literally "Trust/signing
   model — should community plugins be signed? Sandboxed?" and its done-notes confirm it
   shipped `[optional_dependencies]` resolution and trust work. The fold is real, not an
   excuse to drop scope.
4. **A11 decline claim** — `wq show 3934`: done, notes confirm `limpet-workspace export`
   (md/html/json) landed (PR #1913, merged `ebc0064b8f`) *before* wq#3944 closed
   (03:39:51 vs wq#3944's 03:52:48) — the "file" half of A11/G4 the breakdown doc cites as
   already covered genuinely exists and predates the decline decision; it isn't a
   forward reference to unbuilt work.
5. **ISSUE-LOG #1325 citation** — `grep -n "^| 1325 "` on `/home/lumen/kb/ISSUE-LOG.md`:
   row matches verbatim (SearXNG engines suspended/CAPTCHA, no `BRAVE_API_KEY`, GitHub
   401) — the citation is accurate, not fabricated (instinct 8 check).
6. **Sequencing** — wq#3929 done_at `02:57:05`, wq#3944 claimed_at `03:49:01` (research
   genuinely preceded the decomposition it was blocked on); all nine sub-tickets'
   `created_at` (~03:51) fall between wq#3944's claim and its `done_at` (03:52:48) —
   filed during the ticket's own run, not pre-existing or back-dated.
7. **Merge/commit integrity** — `git merge-base --is-ancestor 6499b044 origin/main`
   initially returned false, which looked like an unmerged-PR discrepancy. Resolved: the
   PR was squash-merged to a *different* SHA (`1651c9bb`); `git diff
   6499b044:design/wq3944_parity_breakdown.md origin/main:design/wq3944_parity_breakdown.md`
   is empty — content is byte-identical. The ticket note cites the pre-squash branch-tip
   SHA, which is cosmetically imprecise but not a factual error about what's on main.

## Attempted to break it — the probe

The one *live, falsifiable* claim in the doc is "[search] is still down on 2026-10-15"
(line 37-38 of the breakdown doc). Ran it myself as the probe:

```
/home/lumen/bin/fleet-search web "test query verify wq3973"
```
→ `ok: false`, `unresponsive_engines` lists brave/duckduckgo/google-cse/startpage all
suspended-or-CAPTCHA — reproduces the exact claim, live, on a query the doc's author never
saw. This is the one place the doc could have been fabricating "still down" from the
original wq#3929 research timestamp without re-checking; it wasn't.

Also checked for the failure modes this archetype is prone to: an adopt item silently
dropped (none — all 11 present), a priority contradicting the research's own value/cost
ranking (A2 was moved ahead of A1 within the same p1 tier, but the doc gives an explicit,
plausible reason — A1 doesn't hard-depend on A2's data — and both stay p1, so this is a
sequencing call, not a rank inversion), and a decline/fold dressed up as resolved when the
covering ticket doesn't actually cover it (A9/wq#3945 and A11/wq#3934 both checked out
against the real ticket bodies, not just trusted on the word of the notes field).

## Verdict: CONFIRM

Every factual claim in `design/wq3944_parity_breakdown.md` and in wq#3944's done-notes
checks out against the actual sub-tickets, the source research doc, ISSUE-LOG, and a live
re-run of the one falsifiable claim (search still down). The one anomaly found (squash-SHA
mismatch) resolved to identical content, not a defect. No contradiction found.
