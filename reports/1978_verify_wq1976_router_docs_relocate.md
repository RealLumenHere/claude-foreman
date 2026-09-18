# wq#1978 — verify-pass: wq#1976 router.py research-lane path fix + doc relocation (PR#605)

**Verdict: CONTRADICT** — the router.py fix and 5-doc relocation are correct, but the
relocation left 3 pre-existing documentation links pointing at the old (now-deleted)
`foreman/research/*.md` paths, breaking them. These links worked before this diff and are
dead after it — a regression caused directly by this PR, not a pre-existing defect.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/605 (merged as `6b0bfe6` on main,
squash of PR605's two commits `a62300b` + `3dce9b7`). Diff read cold via `git fetch origin
refs/pull/605/head` and `git diff main...pr605-check` against `/home/lumen/foreman` main,
not the PR's own description of itself.

## What the diff does

1. `orchestrator/router.py:111-118` — rewrites the misleading comment on
   `LANE_REPO["research"]`. The mapping value (`FOREMAN_ROOT`) was already correct; the
   comment previously read as if research docs should land at `foreman/research/NAME.md`,
   when `FOREMAN_ROOT` already **is** `/home/lumen/foreman`, so the correct cwd-relative
   target is `research/NAME.md`. Confirmed against `FOREMAN_ROOT = os.environ.get(...,
   "/home/lumen/foreman")` (router.py:61) and `fworker.py`'s `make_worktree(repo, ...)`,
   which checks out a real git worktree of `repo` — meaning the research lane's worktree
   root has the same top-level layout as this repo (`research/` at root, no `foreman/`
   subdir), exactly as observed in this verify worktree itself.
2. Deletes `foreman/research/brain/pragmatics.md` (280 lines) and `git mv`s 4 other
   misplaced docs (`GLM53_FLASH_EVAL.md`, `LPP_BRAIN_AB.md`, `VENDOR_KEY_ROTATION.md`,
   `VLOGS_PURGE_WQ1747.md`) from `foreman/research/` to `research/`.

## Verification performed

1. **Confirmed the router.py diagnosis is technically correct**, not just plausible —
   traced `FOREMAN_ROOT` and worktree construction (see above). The old comment's "research
   docs land in foreman/research/" reading is what caused wq#1361's original author (commit
   `7c99b23`) and wq#1970's author (ticket text literally said `--deliverable
   foreman/research/VENDOR_KEY_ROTATION.md`) to double-nest. Fix is sound.
2. **Investigated the `pragmatics.md` deletion**, since it stood out as the one file in the
   "relocate 5 docs" set that was deleted rather than renamed (git detected 0 renames for
   it, unlike the other 4 which show as `R100`). Recovered both pre-diff versions
   (`git show 503768c:foreman/research/brain/pragmatics.md` vs.
   `git show 503768c:research/brain/pragmatics.md`) and traced their history: `7c99b23`
   (2026-09-04 15:33 UTC) created the misplaced 280-line copy; a same-day wq#1426 verify
   pass (`25391cd`, 15:40 UTC) contradicted that placement; `f60ea75` (17:13 UTC) landed a
   longer, independently-rewritten 300-line replacement at the *correct* path
   (`research/brain/pragmatics.md`), but the original 280-line file was never cleaned up.
   `diff` between the two versions: 267 of 280 lines differ (near-total rewrite, not a
   superset), but topic coverage is identical (same 10 sections plus the classifier-
   relevance section) — the surviving file is a strict, complete replacement, not a partial
   one. Deleting the stale duplicate instead of `git mv`-ing it (which would have clobbered
   the newer canonical file) was the correct call. This matches the PR author's own
   `kb/ISSUE-LOG.md #399` entry exactly, independently re-derived before reading that entry.
3. **Checked the router.py fix's liveness probe**: ticket wq#1976's own liveness command
   (`git -C /home/lumen/foreman ls-files | grep -c '^foreman/research/' | grep -qx 0`)
   passes on current HEAD — verified directly (`git ls-files | grep -c
   '^foreman/research/'` → `0`).
4. **Checked the VENDOR_KEY_ROTATION.md overlap with wq#1975**: wq#1976's ticket text
   flagged "last one may already be moved by wq#1975, check first." `wq show 1975` is
   still `status: open` (its remaining scope is the fabricated-citation fix, not the path),
   so wq#1976 correctly picked up the move itself — no conflict, no duplicated work.
5. **Tried to break it — searched for dangling references to the 5 old paths across the
   whole repo**, not just the moved files themselves. `grep -rln
   "foreman/research/GLM53_FLASH_EVAL\.md\|foreman/research/LPP_BRAIN_AB\.md\|
   foreman/research/VENDOR_KEY_ROTATION\.md\|foreman/research/VLOGS_PURGE_WQ1747\.md\|
   foreman/research/brain/pragmatics\.md"` across the repo (excluding `reports/` and
   `_runs/`, which are historical narrative, not live pointers) found **3 files still
   citing dead paths**:
   - `ops/wq1573-glm53flash-eval/README.md:3` — `` Full report: `foreman/research/GLM53_FLASH_EVAL.md`. ``
   - `research/GLM53_FLASH_EVAL.md:42` — `` see `foreman/research/LPP_BRAIN_AB.md`) ``
   - `research/ALERT_TRIAGE_LLM.md:272` — `` foreman/research/GLM53_FLASH_EVAL.md:8 ``

   Confirmed `foreman/` doesn't exist at all in this worktree (`ls foreman/` → no such
   file or directory). Confirmed via `git ls-tree -r 503768c` (PR605's parent) that these
   exact paths existed as real files immediately before this diff — so these 3 references
   were valid links before PR605 and are dead links after it, and PR605's own diff (`git
   show 6b0bfe6 --name-status`) never touches any of the 3 referencing files. This is a
   regression this diff introduces, not a pre-existing issue (contrast wq#1974's finding,
   which was pre-existing and explicitly not grounds for contradiction there).
6. **Independent escalation** (protocol: contradiction escalates to opus-5@high). Spawned a
   fresh opus-5@high agent blind to this report, given only the 3 broken-reference claims
   plus the before/after existence check to verify from primary sources. It returned TRUE
   on all 4 sub-claims (3 dead references + pre-diff validity), independently re-deriving
   the same blob hashes and `git show --name-status` evidence.

## Severity assessment

The core fix (router.py comment + 4-of-5 doc relocations + the `pragmatics.md` collision
resolution) is correct and well-reasoned — better than the ticket's own bar, since the
author caught and correctly handled a collision the ticket text didn't anticipate. But the
same "discoverability" goal this whole fix-chain exists for (wq#1972: "any future tooling
that globs `research/*.md` will silently miss this deliverable") cuts the other way here:
a human or agent following any of these 3 now-dead links gets a file-not-found, on docs
that worked before this PR touched anything. It's a small, mechanical gap (3 lines across
3 files) but it's real, reproducible, and directly caused by this diff.

## Conclusion

CONTRADICT. Filed wq#1979 (build lane, no deps) to fix the 3 dangling references. Did not
re-implement per this ticket's instruction — verify only.
