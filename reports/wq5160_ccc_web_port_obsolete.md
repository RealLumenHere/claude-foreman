# wq#5160: ccc-web feature port is obsolete (wq#5159 = C, shelve)

Ticket: wq#5160 "[ccc-web] port remaining Electron features (sub-PRs) if wq#5159 says proceed".
Date: 2026-11-22. Worker: fworker-wq5160.

## Result

Not ported, on purpose. The ticket only applied if wq#5159 said **proceed**. wq#5159 closed
2026-11-21 17:21 UTC with **DECISION C: shelve ccc-web**. Nothing on this list goes into ccc-web.

## Why (evidence from wq#5159 notes)

- Memory, Thor GPU (Iris Xe, Mesa accel), 6 tiles on claude.ai/new, 90 s settle, sudo PSS:
  Electron 1195 MB, ccc-web 2183 MB (7 WebKitWebProcess), so **1.83x**. Frank's rule says
  >=1.3x means C.
- Signed-in run, Thor GPU, sudo PSS: ccc-web 4481 MB against an Electron live-derived figure
  of ~2230 MB, so **2.0x**. That confirms C.
- Parity blocker: Google sign-in fails inside a WebKit tile (Frank's screenshot, 09:16 PST).
- UX: Frank, 09:44 PST 2026-11-21: "Electron CCC navigation bar is WAY better than
  ccc-web's. C is final on memory AND UX." wq#5214 (port ccc-web's sidebar to Electron)
  was closed obsolete for the same reason.

## Features listed in this ticket

None of these is lost. Each one already ships in Electron CCC, the shell that stays:

| Feature | ccc-web | Electron CCC |
|---|---|---|
| broadcast box | not ported (shelved) | live |
| staged prompt queue (QUEUE_AUTO_ADVANCE=false) | not ported (shelved) | live |
| move palette | not ported (shelved) | live |
| publish pad + exporter | not ported (shelved) | live |
| hourly chat-list snapshot -> session-briefing ingest-chats | not ported (shelved) | live |
| ntfy bell feed + tile-finished ping | not ported (shelved) | live |
| metrics dash + JSONL | not ported (shelved) | live |
| seam-drag resize | not ported (shelved) | live |
| OR-Set ccc-sync | not ported (shelved) | live |

## Where the memory work went instead

Outcome C's follow-up is wq#5213, done as lumen/ccc PR #54 (merge 02057f6). It lowered
MAX_LIVE_TILES from 8 to 5, added idle sleep for off-screen tiles, made wake restore keep
drafts, and moved the chrome views onto one shared renderer. The Thor root PSS A/B for that
work is wq#5231.

ccc-web keeps running on Thor only as Frank's detached manual instance from 2026-11-21. It is
not autostarted and gets no further feature work. Reopen this ticket only if a new decision
ticket reverses wq#5159.
