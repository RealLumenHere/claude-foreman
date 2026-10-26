# wq#4649 — verify-pass of wq#4645 (ccc chrome de-clutter: tools menu)

**Ticket under verification:** wq#4645 — tools menu replaces 5 topbar buttons,
broadcast auto-closes, compact bottom bar.
**Nominal deliverable:** http://100.121.17.5:3000/lumen/ccc/pulls/13 (commit
`1ef7391`, branch `feat/wq4645-ccc-chrome-de-clutter-tools-menu-replace-a1`).
**Verdict: CONFIRM** — the done-notes' central claim holds up under independent
check, and no correctness bug was found in the PR#13 diff itself. No escalation
to opus-5@high; no judgment file.

## What wq#4645's own done-notes claim

The author session recorded that this ticket was a **duplicate dispatch**: the
scope landed directly to main as `f220830` ("fix(chrome): tools menu, broadcast
auto-close, compact bar", 2026-10-26 11:33:06) five minutes *before* the same
session also pushed `1ef7391` on PR#13 (11:38:41) without knowing the first one
had already shipped. The notes say PR#13 conflicts with main on rebase and was
deliberately left open/unmerged rather than landed, to avoid reintroducing a
duplicate/conflicting implementation. They also say a PR comment to record this
failed with HTTP 403 (broker token lacks `write:issue`).

This verify-pass does not take that account on faith (instinct 4: an author's
self-report is not evidence until checked). Each claim was independently
reproduced:

1. **"Already landed as f220830 before PR#13's commit existed."**
   Confirmed via `git log`: `f220830` (11:33:06) predates `1ef7391` (11:38:41)
   by 5 minutes, both on `Refs: wq#4645`, both touching the identical five
   files (`src/config.js`, `src/main.js`, `src/preload-shell.js`,
   `src/renderer/bottombar.html`, `src/renderer/topbar.html`).

2. **"Landing PR#13 now would conflict with main."**
   Reproduced directly: `git merge-tree main feat/wq4645-ccc-chrome-de-clutter-tools-menu-replace-a1`
   returns real `CONFLICT (content)` in `src/config.js`, `src/preload-shell.js`,
   `src/renderer/bottombar.html`, and `src/renderer/topbar.html` — the exact
   four files the notes named. Not a guess; this is the actual merge engine's
   output run fresh in this session.

3. **"Broker token lacks write:issue, so the PR could not be annotated."**
   `mcp__gitea__pull_request_read` was denied in this very session
   ("Claude requested permissions ... haven't granted it yet" — the headless
   worker's tool allowlist has no `gitea` MCP entries), consistent with the
   same write/read-scope gap reported by the author. Prior rows in
   `/home/lumen/kb/ISSUE-LOG.md` (e.g. #808: "`lumen-pr comment ... 1080`
   failed HTTP 403 because the broker token has scope write:repository,
   write:user and PR comments need write:issue") document this as a known,
   recurring gap, not a one-off excuse.

## Adversarial review of PR#13's diff itself (archetype-8 "try to break it")

Read the full diff cold (`git show 1ef7391`) and tried to find a correctness
bug independent of the duplicate-dispatch question, in case this PR is ever
revived:

- `node --check` on the three extracted `.js` files (main, preload-shell,
  config) — all parse clean.
- Extracted the inline `<script>` blocks from `topbar.html` / `bottombar.html`
  with `new Function(...)` — both parse clean.
- Cross-checked every `$('#id')` DOM reference against the ids actually
  defined in the same file — no stale reference to a removed button
  (`#bcbtn`, `#qbtn`, `#wqbtn`, `#dashbtn`, `#side` are all correctly removed
  together with their only referencing code).
- Cross-checked every `window.ccc.send(...)`/`window.ccc.on(...)` channel used
  in the two HTML files against `preload-shell.js`'s `ok` allowlists — no
  channel is silently dropped (a classic way this kind of change ships
  broken: a new call added to the renderer but forgotten in the preload
  bridge's allowlist). All match.
- Traced the new `ccc:toolsMenu` handler's closures (`lastWq`, `dashOpen`,
  `tabs`, `activeId`, `win`) — all module-top-level `let`s assigned during
  `boot()`/`pollWq()` well before any menu click can fire; no
  use-before-initialization.
- The inline "Toggle sidebar" menu item duplicates the existing
  `ipcMain.on('ccc:toggleSidebar', ...)` handler's body instead of calling it —
  a minor DRY nit (the landed `f220830` instead calls
  `ipcMain.emit('ccc:toggleSidebar', null, activeId)` to reuse it), not a
  correctness bug. The old handler itself is left registered but now has no
  caller — harmless dead code, not a behavioral defect.
- `BARS_FULL` propagation to the renderer differs in *mechanism* from
  `f220830`: PR#13 mirrors the flag through a synchronous `contextBridge`
  (`window.cccCfg.BARS_FULL`), read once at script load — always correct
  regardless of timing. `f220830`'s mechanism (`bottombar.webContents.send('ui',
  {full:true})` fired once from `boot()`) is a plausible load-order race since
  `webContents.loadFile` is async and nothing confirms the renderer's `'ui'`
  listener is attached yet when `boot()` sends it — but `CFG.BARS_FULL`
  defaults to `false` in both versions, so this path is currently dead code in
  production and out of scope for *this* ticket's verify-pass (it would need
  its own ticket against `f220830`, flagged here for visibility, not filed
  separately since it has no live effect today).
- Scope is narrower than `f220830` in a few places (no `Ctrl+Shift+M`
  accelerator for the menu, no terminal/solo/copy-last items in the menu, the
  broadcast topbar height stays at 150px instead of shrinking to 112px) — none
  of these are bugs, they're simply smaller-scope choices than the sibling
  implementation. The core ask (native menu replaces the 5 buttons, broadcast
  auto-closes on send, low-signal bottom-bar blocks fold into tooltips behind
  `BARS_FULL`) is fully and correctly implemented in PR#13 too.

No automated probe exists for this ticket (wq#4645's `deliverable` field is
`None`, and the repo's test suite — `test/{queue,ntfy,wq}.test.js` — covers
unrelated modules, not the chrome/topbar/bottombar UI). Verification here is
by static trace plus a live `merge-tree` dry run, not a runnable acceptance
script.

## Conclusion

CONFIRM. `f220830` is the real, already-shipped fix for wq#4645; PR#13 is a
genuine duplicate produced by the same session racing itself on the same
lease, and merging it now would reopen conflicts in exactly the files
predicted. Correctly left open and unmerged. No bug found in PR#13's own diff
that would change this recommendation. Action for a human/future ticket:
someone with `write:issue` scope should close PR#13 referencing `f220830` so
it stops showing as open work; this session's broker token cannot do that
(same gap documented in ISSUE-LOG #808).
