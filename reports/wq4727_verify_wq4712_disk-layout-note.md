# wq#4727 — verify-pass on wq#4712 (PR#2516): disk-layout note symlink claim fix

Archetype 8 adversarial verify. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance). Verdict: **CONFIRMED** — no contradiction, no escalation.

## What wq#4712 claimed
`claude_memory.md`'s `misc:disk-layout-rules` block (and the `disk-layout.rules` fact
it was hand-pasted from) wrongly said `/home/lumen/{projects,worktrees,esphome,go}` are
symlinks into `/scratch/fleet/*`. Fix: correct both the `lumen_state.py` fact and the
`claude_memory.md` generated-zone block + hand-prose note to say these four paths
(+`/home/lumen/scratch` itself) are real root-LV directories, without migrating any
data. Filed wq#4723 as a follow-up to check `/home/lumen/minerva/context/fleet_tools.yaml`
for the same claim (out of wq#4712's worktree add-dirs).

## What I verified independently (fresh evidence, this session, 2026-10-27)

1. **Core claim, re-run from scratch:**
   `readlink -f /home/lumen/{projects,worktrees,esphome,go,scratch}` → each resolves to
   itself. None is a symlink. Matches the fix.
2. **Counter-claim (paths that ARE symlinks), re-run from scratch:**
   `readlink -f /home/lumen/{models,gocheckin,sandbox,simpill-scratch-preserved,build,android-sdk,re}`
   → all resolve into `/scratch/fleet/*` or `/data/lumen/archive/re` as claimed.
3. **Mount evidence:** `df -h` on `scratch`/`projects`/`worktrees`/`esphome`/`go`/`foreman`/
   `projects/limpet` all show `/dev/mapper/ubuntu--vg-ubuntu--lv` (root LV, 466G, 74%
   used, 118G free — matches the ~119G claimed, drifted 1G since the ticket). `/scratch`
   = `/dev/md1` 1.9T 39% used; `/data/lumen` = `/dev/md0` 469G 78% used — both match.
4. **Live state fact:** `lumen_state.py current disk-layout rules` returns the corrected
   value verbatim; `lumen_state.py history disk-layout rules` shows the prior
   (2026-10-02, pointer-only, no per-path list) and new value exactly as the ops bundle's
   `disk_layout_rules_fact_after.md` describes — bi-temporal history intact, nothing
   lost.
5. **Live file edit:** Read `/home/lumen/claude_memory.md` directly (Bash `grep`/`wc` on
   this path is sandbox-refused, as the PR's README documents — confirmed the same
   asymmetry independently) — the `misc:disk-layout-rules` block (lines 273-292) and the
   hand-prose `### 2026-10-27 disk-layout fact corrected (wq#4712)` entry (lines 517-520,
   inside the `hand-prose:begin/end` markers) match the ops bundle's claimed after-text
   verbatim.
6. **ISSUE-LOG:** row 1608 in `/home/lumen/kb/ISSUE-LOG.md` matches the symptom/cause/
   solution/status claimed.
7. **Follow-up ticket:** wq#4723 exists, status `open`, scoped correctly (needs
   `dir(/home/lumen/minerva)`, not claimed by wq#4712's worker — correct given its
   add-dirs).
8. **Merge:** 36b261bb is on `main`; every active worktree branch (including this one)
   contains it.

## Things I tried to break, that held up
- **Build-cache consequence numbers:** `du -sh` confirms `projects/limpet/iso-build/.build`
  = 20G exactly as claimed. The "foreman/.build (28G)" shorthand is imprecise — the real
  path is `/home/lumen/foreman/limpet/iso-builder/.build` (nested, not directly under
  `foreman/`) — but `du -sh` on that exact path returns 28G, matching the number exactly.
  Cosmetic path-shorthand looseness in prose, not a factual error in the corrected memory
  facts (which don't repeat this shorthand at the predicate level) — not a contradiction.
- **Checked wq#4723's premise directly:** read the entirety of
  `/home/lumen/minerva/context/fleet_tools.yaml` (303 lines) and the start of its
  rendered `FLEET_TOOLS.md` — neither currently contains any symlink claim for
  projects/worktrees/esphome/go. wq#4712 never claimed to have confirmed the defect
  there; it explicitly flagged the path as *unchecked* (outside its add-dirs) and filed
  a follow-up rather than asserting a fix. wq#4723 may resolve as a non-finding, but that
  does not make wq#4712's own deliverable false — it was honest about what it did and
  didn't check.

## Verdict
CONFIRMED. All four corrected facts match live, independently-reproduced ground truth;
the live-state and live-file edits are present and correct; the paper trail (ISSUE-LOG
row, follow-up ticket) is accurate. No escalation to opus-5@high.
