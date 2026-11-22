# wq#5340 verify of wq#5327: stack layout topbar chips and range label

**Verdict: CONFIRMED.** No contradiction, no escalation.

- Diff under test: ccc PR #58, branch head `5bb4483d`, squash-merged as `1128770` on main. `git diff 5bb4483d 1128770` is empty, so the merged code is the reviewed code.
- Files: `src/main.js:515`, `src/renderer/topbar.html:176-193,255-257,298`, `test/topbar-strip-view.test.js`.

## What the fix does
- `pushTabStrip` now sends `visibleIds`. In stack mode it is `lastLayout.visible` ids, and `layoutStack` (`main.js:354-355`) builds that list from `computeStack().rects`, with rects[0] as the primary. In every other mode it is null.
- `stripView(st,row)` checks solo first, then stack mode with `visibleIds` (marks exactly those ids, label `<big> + <min>–<max>/<n>`), then falls back to the old contiguous `[pageOffset, pageOffset+columns)` logic.

## Evidence
1. The PR's own test passes: `node --test test/topbar-strip-view.test.js` gives 6/6.
2. **The probe can fail.** I disabled the stack branch (`if (false && ...)`) and the `visibleIds` line in `main.js`, then reran the PR test: 4 of 6 failed (repro, mid-row paging, single tab, main.js send). Row and solo still passed, as they should.
3. **Adversarial sweep** (`reports/wq5340_verify_wq5327_probe.js`, pinned to a detached checkout of `1128770`): it runs the real `stripView` against `computeStack` for n=0..12, bottomN=1..6, every primary including null, and offsets -1..n+1 (out-of-range offsets test the clamp). **6006 cases, 0 bad**, covering both the onscreen set and the label. With the stack branch disabled the same sweep reports 10876 bad.
4. Full suite: 100 tests with 99 passing at `1128770`, 94 with 93 passing at `bc2e86b` (pre-fix). The one failure is `test/harness/harness.js` failing with `Cannot find module 'electron'` because a fresh worktree has no node_modules. It fails the same way on both revisions, so it is environmental and not a regression.

## Edge cases checked
- **Solo while stack mode is on.** `layoutRow` takes the solo branch, so `lastLayout.stack` is undefined and `visibleIds` is null. `stripView` returns the solo result before it reaches the stack branch.
- **Leaving stack mode.** `setStackMode(false)` calls `relayout()`, which rewrites `lastLayout` without `stack`. `visibleIds` goes back to null and the topbar uses the old row logic.
- **Strip pushes that skip relayout** (`stripRefresh`, color, status, title at `main.js:174,1827,1934`). They reuse `lastLayout`, which still describes what is on screen, so the chips stay right.
- **Closing a tab.** `closeTab` calls `relayout()` (`main.js:~1042`), which recomputes `visibleIds`.
- **Stale ids.** If `visibleIds` holds a closed primary, the sweep shows no crash and no wrongly lit chip. The label shows `–` until the next relayout, which is harmless.
- **Empty row in stack mode.** `visibleIds` is `[]` and the label is `–`.

## Out of scope (pre-existing, not part of wq#5327's diff)
- **Pager bound on status, title and color pushes.** These send `maxOffset = tabs.length - columns`, which counts all tabs instead of the row (`main.js:1827,1934`). Under an orchestration filter, `#next` can stay enabled one click too long. `ccc:page` clamps on `rowTabs()` (`main.js:1804`), so the extra click does nothing. This applies in row mode as well. It is cosmetic and was not filed.
- **Merge trailer.** Squash merges on ccc and foreman main carry a `Co-authored-by: Lumen Industries` trailer, added at merge time; all 8 of the last 8 ccc main commits have it. `lumen-pr`'s `lint_no_coauthor` checks commits and PRs but not the merge commit Gitea writes. Filed as wq#5342.
