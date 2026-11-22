# wq#5324 verify of wq#5215: Electron CCC stack layout (big tile on top, smaller row underneath)

Verifier: fworker-wq5324 (fresh instance), 2026-11-22. PR: http://100.121.17.5:3000/lumen/ccc/pulls/57.
Landed on main as `bc2e86b`, a squash of PR head `8950ad2`. `git diff 8950ad2 bc2e86b` is empty, so main has exactly what was reviewed.

## Verdict: CONFIRMED, with one minor defect filed as follow-up wq#5327

Every claim in the wq#5215 done notes holds:

| Claim | Evidence |
|---|---|
| The stack layout exists, toggled by Ctrl+Shift+L and the tools menu | main.js:2738 (key; no earlier `'l'` binding in the Shift branch at fa4124f), main.js:1630 (menu checkbox), main.js:321 (layoutRow dispatches to layoutStack when the layout is not solo) |
| One big tile on top, 4 smaller tiles below (`STACK_BOTTOM_TILES`), configurable | config.js:33-34; stack-layout.js:21-51 |
| The big tile follows an explicit pick only, never focus | main.js:1106-1110: only `bringIntoView && !adopt` sets `stackPrimaryId`. The focus handler at main.js:778 and the keyDown adopt handler at :779 call `activate(id, {})`, so they never promote. Ctrl+1-9 (:2730), chip click (:1796) and wake (:2069) pass bringIntoView and do promote. The chip menu also has "Make this the big tile" (:1366). |
| The layout persists across restarts | snapshot main.js:1709-1710, restore :2619 and :2634. Restore keeps tab order (saved list reversed, then `unshift`), so the saved index maps back to the same tab. |
| Tests 6/6 | `node --test test/stack-layout.test.js`: 6 pass, 0 fail. Full suite `node --test` in /home/lumen/ccc: 93 of 94 pass. The 1 failure is `test/harness/harness.js` with `Cannot find module 'electron'`. That file is an Electron harness the glob also picks up, it was last touched in 708d89e, and this diff does not change it, so the failure predates this PR and is caused by the environment. |

Paging stays consistent with the stack geometry. sendPage/ccc:page compute `maxOffset = rowLen - computeColumns()`, and computeColumns returns `lastLayout.visible.length` (1 + bottomN). That gives `rest.length - bottomN`, which equals computeStack's maxOffset. relayout also clamps again at main.js:457. Seam handles drop to 0 in stack mode (`syncHandles(1, ...)`). Sleep placeholder cards get y/h per slot (sleep.html:23).

## Adversarial probe (falsifiable)

Script: `reports/wq5324_verify_wq5215_probe.js`. Run it with `node <probe> <ccc checkout at bc2e86b>`; it was run against `/home/lumen/worktrees/wq5324-a1`, which is pinned at bc2e86b.

- **Sweep:** rowW in {1599, 1600, 1601, 333, 97}, 1-12 tabs, every primary, every offset. For each case it checks four things: no overlap, every tile in bounds, the primary stays in the top slot, and every non-primary tab can be reached by paging. Result: **0 failures**.
- **Mutant check:** changing the bottom-row `y` to `top + topH - gap` makes the same sweep report **1785 failures**, so the probe can fail.
- **Degenerate inputs (cosmetic only):**
  - When `midH < gap`, the top rect gets a negative height (-3 at midH=4, -5 at midH=0). Electron's gfx::Size clamps negative sizes to 0, so nothing breaks.
  - `STACK_BOTTOM_TILES: 0` silently falls back to 4.
  - `STACK_TOP_FRAC: NaN` produces a NaN height. This can only come from a hand-edited config.

## Findings

1. **Minor UI defect, filed as wq#5327.** topbar.html was not updated:
   - Chip dimming at :232 still assumes the visible tiles are one contiguous run `[pageOffset, pageOffset+columns)`.
   - The range label at :273-275 makes the same assumption.
   - In the stack layout the visible set is primary + `rest[offset..]`, which is not contiguous.
   - Repro: 7 tabs, primary = tab 7. Actually visible: [7,1,2,3,4]. Chips marked onscreen: [1,2,3,4,5]. The big tile's chip is dimmed as offscreen, and hidden tab 5 shows as onscreen.
   - Only the indicator is wrong; layout and behaviour are unaffected.
2. **Nit:** the comment at stack-layout.js:38-39 says slots are "sized for bottomN even when fewer tiles remain". The code does the opposite: `slots == bottom.length` always, and with 3 tabs the 2 small tiles get 796 px each. The test at :49-51 encodes the code's stretch behaviour, which is reasonable. The comment is wrong, not the code.
3. **Nit:** the comment at main.js:2633 says "ids are re-minted on restore". They are not: main.js:564 reuses `restoreMeta.id`. Restoring by index still works.
4. **UX edges, not defects:**
   - layoutStack writes the row[0] fallback back into `stackPrimaryId` (main.js:353). So switching the group filter to a group that lacks the big tile forgets the big tile when you switch back.
   - Closing the big tile promotes row[0], not the newly active tab.
5. The ticket asked to "confirm with Frank before building". The author chose the defaults autonomously under instinct 3, recorded them in stack-layout.js:6-13 and the done notes, and made both configurable. That is consistent with the full-autonomy order.
