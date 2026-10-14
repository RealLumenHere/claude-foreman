# wq#3897 — verify-pass of wq#3893: land wq#3876 HALucinator eval doc to main

**Verdict: CONFIRMED.** `research/HALUCINATOR_EVAL.md` is now on `origin/main`, byte-identical
to the content wq#3887 already verified factually accurate, landed as a single scoped commit
with no unrelated changes, and the stale source branch/PR were cleaned up as claimed.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1870 (unauthenticated `curl` against
both the web UI and `/api/v1/repos/...` 404'd — this Gitea instance doesn't serve anonymous
reads and I have no token to spend here; verified via git history instead, which is the
ground truth for "did it land" anyway).

## What wq#3893 claimed

Land the existing (but never-merged) `feat/wq3876-...-a1` branch's doc to main. PR #1857 had
gone stale (20 commits behind), so wq#3893 opened a fresh PR #1870 from a rebased commit
(`070c238d`), merged it (landing as `d2542fef` on main), closed #1857, and deleted the stale
branch. Claimed zero content edits, citing that the doc already self-caveats its wq#3850
(`limpet/examples/halucinator.toml` / `tool-catalog.json`) citations as describing unlanded
branch state.

## What I checked, independently of the diff's own narration

1. **Confirmed the file is actually reachable from main**, not just present on a branch:
   `git log --oneline -5 origin/main` lists `d2542fef docs(limpet): wq3876 evaluate
   HALucinator...` directly, and `git show origin/main:research/HALUCINATOR_EVAL.md` returns
   the full 154-line doc. This is the exact gap wq#3887 found (content existed only on an
   unreachable branch) — now closed.
2. **Byte-diffed content across all three points in the chain**, not just trusted the
   ticket's claim of "no edits":
   - `git diff 5cde9978 070c238d -- research/HALUCINATOR_EVAL.md` → empty (original
     wq#3887-verified commit == wq#3893's rebased branch commit).
   - `git diff 070c238d d2542fef -- research/HALUCINATOR_EVAL.md` → empty (branch commit ==
     landed main commit).
   So the content on main today is provably identical to the version wq#3887 independently
   re-verified against GitHub/PyPI — no drift introduced by the rebase/re-PR.
3. **Checked the landed commit is scoped**: `git show d2542fef --stat` → one file,
   `research/HALUCINATOR_EVAL.md`, 154 insertions, 0 deletions. No incidental changes rode
   along.
4. **Checked ancestry is clean, not a dangling/orphan merge**: `git merge-base --is-ancestor
   070c238d^ origin/main` → yes; `d2542fef^` is `2b3250e8` (the prior main tip). Linear
   history, no merge-commit weirdness, nothing skipped.
5. **Verified the self-caveat claim rather than trusting it**: `git show
   origin/main:limpet/examples/halucinator.toml` → "does not exist in 'origin/main'" (wq#3850
   genuinely still unmerged). Grepped the landed doc for the caveat language: lines 8-10
   ("catalog stub landed by wq#3850 ... not yet merged to main as of this ticket") and line
   152 ("branch `feat/wq3850-...` (not yet merged — read via `git ... show
   origin/feat/wq3850-...`)") both present and accurate. wq#3893 did not need to add a caveat
   because one already existed and still matches live repo state.
6. **Confirmed branch cleanup**: `git ls-remote origin 'refs/heads/*wq3876-limpet-evaluate*'`
   returns nothing — the source branch is gone from origin, matching the "deleted after
   confirming the merge" claim. The local worktree still has a stale local-only ref, which is
   expected (local refs don't auto-prune) and not evidence of anything wrong.
7. **Checked `wq show 3893`**: `deliverable: research/HALUCINATOR_EVAL.md`, `status: done` —
   the done-gate that wq#3887/wq#3893 exists specifically to close (wq#3876 was originally
   marked done with `deliverable=None`, letting it skip the on-main check) is now populated
   and the file genuinely satisfies it, not bypassed.
8. **Checked kb/ISSUE-LOG.md row 1308** (the WebFetch-refusal workaround note wq#3893's ticket
   also asked to confirm lands): already present in `/home/lumen/kb/ISSUE-LOG.md`. This is a
   separate repo from `claude-foreman` with its own append-only, lock-protected write path
   (`issue_log_append.py`), not something that rides on a foreman-repo branch merge — it was
   never actually blocked on this land, so there was nothing for wq#3893 to do there.

## What would have broken this verdict (and didn't)

- If `git log origin/main` didn't contain the commit, or `git show origin/main:<path>` 404'd,
  the land would still be fake regardless of what the PR page says — checked directly, it's
  there.
- If the three-way content diff (original → branch → main) showed any hunk, "no content
  edits" would be false — all three diffs were empty.
- If `limpet/examples/halucinator.toml` actually existed on main now, the doc's "not yet
  merged" caveat would itself be stale/wrong — checked live, it still doesn't exist.
- If the landed commit's stat showed files beyond the eval doc, that would indicate scope
  creep in the land — stat shows exactly one file.
- If the source branch still existed on origin, the "deleted after confirming" claim would be
  false — `ls-remote` confirms it's gone.

No escalation to judgment needed — board this as verified.
