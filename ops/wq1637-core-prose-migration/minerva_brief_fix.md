# minerva_brief.py — generated-section labeling + dedup-on-write

Cannot be implemented from this worktree: `/home/lumen/minerva/context/minerva_brief.py`
lives under `/home/lumen/minerva`, which `harness/fleet-exec-policy.yaml:67` deliberately
excludes from every session's read/write roots (wq#1467 — credentials mirrored into
context/sections). This is a description for whoever holds that grant to implement, per
MINERVA_PIPELINE_MAP §4.4's projection contract (every generated artifact needs a
`generated-from` header + a single-writer regen path).

## Evidence this is needed

1. `/home/lumen/claude_memory.md:29-83` — the live daily brief, wrapped
   `<!-- MINERVA_BRIEF_START -->` / `<!-- MINERVA_BRIEF_END -->`, prepended at the very top of
   the file, dated to the day it ran. No `generated-from:` header — a reader can't tell this
   block is a projection rather than hand-typed content.
2. `/home/lumen/claude_memory.md:399-447` — a **second, stale** `MINERVA_BRIEF_START/END` pair
   found sitting inside an unrelated `<!-- SECTION:MESSENGER_WEBAPP_2026_04_23 START -->`
   wrapper, containing proposal content dated 2026-07-04/07-05 — roughly two months stale
   relative to the "last 24h" framing inside its own text. Direct proof the prepend mechanism
   has never cleaned up an earlier copy of itself; there may be others not found by this pass
   (this session read the whole file once, start to finish, but did not exhaustively grep for
   every possible `MINERVA_BRIEF_START` occurrence — grep against the real file is blocked
   from this sandbox the same way `ls`/`find` are; a session with `/home/lumen` proper access
   should run `grep -n MINERVA_BRIEF_START /home/lumen/claude_memory.md` to get an exact count
   before treating "found 2" as final).

## Proposed change

In whichever function performs the daily prepend (per `## MINERVA_SYSTEM` at
claude_memory.md:375-397: "minerva-brief (daily 13:00 UTC) -> briefings/YYYY-MM-DD.md + auto-
prepend to this file"):

1. **Before inserting**, scan the full file content for every existing
   `<!-- MINERVA_BRIEF_START -->...<!-- MINERVA_BRIEF_END -->` span (not just "yesterday's",
   which is presumably how the stale duplicate survived — the scan must not assume there is
   at most one prior copy in a known location) and remove all of them.
2. **On insert**, write the header immediately inside the opening comment:
   `<!-- generated-from: minerva.db proposals table @ <ISO-8601 timestamp> -->`, matching the
   pattern MINERVA_PIPELINE_MAP §4.4 already specifies for the other generated artifacts
   (LIVE_FLEET_STATE, HANDOFF's generated half, etc.).
3. Alternative shape, if the daily brief's actual value is better served living outside the
   core-selector budget entirely: write only to `briefings/YYYY-MM-DD.md` (already happens)
   and prepend a single one-line pointer to claude_memory.md instead of the full brief body —
   "See /home/lumen/minerva/briefings/<date>.md for today's brief (generated <time>)." This
   avoids the CORE-budget cost of the DEFER/BLOCKED proposal list on every session that never
   needs it, while keeping same-file discoverability. Ticket text names this as the preferred
   option ("or writes to briefings/ with a one-line pointer") — recommend this over option 2
   unless something specifically depends on the full brief being inline.

## Not in scope for this diff

Deleting the *current* (non-stale) brief block at lines 29-83 — it's live content, correctly
placed, just unlabeled. Only the stale duplicate at 399-447 is deleted by
`diffs/03-delete-stale-brief-duplicate.diff`, independent of when/whether this script fix
lands.
