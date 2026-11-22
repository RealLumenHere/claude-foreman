# wq#5232: verify of wq#5213 (ccc PR #54, tile-sleep + shared chrome renderer)

Verdict: **CONFIRM.** No contradiction. Three minor residual notes are listed below. None is a regression against pre-#54 behaviour.

- Diff verified: lumen/ccc `4c3907f` (= origin/main; tree-identical to the branch commit `02057f6` cited in the done notes).
- Worktree: `/home/lumen/worktrees/wq5232-a1`.
- Electron 41 came from `/home/lumen/worktrees/wq5213-a1/node_modules` through a symlink. The symlink is untracked and was removed after the run.
- Verifier: claude-sonnet-5-5 lane, run as anthropic/claude-opus-5-5 by fworker. 2026-11-22.

## Probes run (all on Lumen, xvfb)

| Probe | Result |
|---|---|
| `node --test` | 83/84 pass. The only failure is `test/harness/harness.js`: `Cannot find module 'electron'` under plain node, an environment issue unrelated to #54. `test/sleep-policy.test.js`: 10/10. |
| `npm run probe:chrome-share` | ALL OK: 5 window.open'ed views share the host pid. Each has `window.ccc` (preload). IPC works main→view and view→main, with `e.sender` = the view. 1 renderer for 6 chrome views. The standalone control gets its own pid. `../main.js` is refused. |
| `npm run probe:app-chrome` (real main.js, 8 tabs) | ALL OK: topbar, bottombar, sidebar and 2 handles all on one pid. Live chat tiles = 5 (cap). Tiles never share the chrome pid. |
| Own probe `reports/5232_verify_wq5213_sends_probe.js`, shared vs `--solo` (= `CCC_SHARED_CHROME=0`) | The control run gives separate pids (topbar/bottombar/sidebar 2352030/2352031/2352037), so the pid check can fail. The IPC channels delivered after load are identical in both modes: bottombar `deepseek fleet ntfy queue sysstats tileStatus wq`, sidebar `frankGated sidebar tasks`, topbar `collapse tabs wq`. |

To re-run the probe, add an npm script `electron <abs path to probe> --no-sandbox` under `xvfb-run` in a ccc checkout of `4c3907f`. Arguments: `-- --solo` and `-- --n=60`.

## Attack: did the async boot break the initial-data push to chrome views?

- **Concern.** `boot()` is now `async`, and bottombar and sidebar start loading during `await openSharedChrome()`. Before #54, `loadFile` and the `did-finish-load` listener registrations ran in the same synchronous tick. The listeners at main.js:2093 (queue), 2319 (wq), 2394 (tasks+gated) and 2419 (ntfy) could now register after the load had already finished, and their initial data would never be pushed.
- **Method.** I instrumented every `did-finish-load` registration (call site and time) and every load event.
- **Result.** All registrations land before the load (`afterLoad: false`) at N=8, 60 and 300 tabs:

  | Tabs | Listeners registered | bottombar load |
  |---|---|---|
  | 8 | 476–500 ms | 635 ms |
  | 60 | 854–890 ms | 1309 ms |
  | 300 | 2183–2214 ms | 10135 ms |

- **Why it holds.** The load event completes on the main loop, which the synchronous rest of `boot()` keeps busy. The gap therefore grows with tab count instead of shrinking.
- **Status.** Not broken. In principle it is a race with no ordering guarantee (note 1).

## Residual notes (minor, not regressions)

1. **Latent ordering.** Nothing guarantees that the chrome listeners register before a shared view's load finishes. Measured margin: at least 135 ms, and it grows with load. A cheap hardening for later would be to send the initial payloads directly when `!wc.isLoading()`.
2. **Draft-restore gap.** In `draftFromHeartbeat` (src/sleep-policy.js:89), `_restoreTries > 0` counts as "our inject ran", even when that try was acked `ok:false` (composer not mounted yet). If claude.ai then mounts the composer with its own older stored draft, that text wins and our copy is dropped. Demonstrated with `node -e`: after a failed try, `draftFromHeartbeat(tab, 'hel')` returns `{draft: 'hel', pending: null}` for a pending `'hello world'`. The old code lost the draft in strictly more cases (any missed inject), so this is an improvement with a remaining hole. A fix would track "last restore ack ok" instead of the try count.
3. **Shared failure domain.** One renderer crash now takes down every chrome view at once. Before #54 there was also no `render-process-gone` recovery for chrome views; main.js:688 covers tiles only. So the blast radius is bigger, but the recovery story is unchanged.

Also checked and fine:
- `capVictims` and `idleVictims` exclusions: active, visible, pinned (idle rule only), and busy (working/continue/limited) tiles are never victims.
- An `error`-status tile can idle-sleep, the same as under the old cap rule.
- `envInt` rejects negative and non-finite values.
- `sleepOutsideRow() + sleepIdleOffscreen()` is numeric.
- Pooled handle reuse is guarded by `_keysAttached`.
- The fallback to a standalone `makeChromeView` on an empty pool or a timeout works.
- The topbar's new `setWindowOpenHandler` denies everything except our own renderer pages.
- The idle rule does not honour `SLEEP_AUTO=false`. That is by design: it has its own knob, `CCC_IDLE_SLEEP_MIN=0`, and the ticket asked for idle auto-sleep.

The acceptance item "sudo PSS before/after on Thor" was honestly split out to wq#5231 (pending_review). It is not claimed by #54.
