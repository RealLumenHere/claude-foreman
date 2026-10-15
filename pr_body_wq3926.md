## What
Investigation report for wq#3926 (ptah `ncs2-vision` container unhealthy), plus an ISSUE-LOG entry (row 1354) documenting that ptah itself is unreachable.

## Why
ptah's `ncs2-vision` container has been `(unhealthy)` with `/health` 503 since 2026-10-12, and `claude_memory` incorrectly lists it as healthy. The ticket asked for `docker logs`/`lsusb`/MYRIAD-device checks, but ptah has not returned from its 2026-10-15 02:54 UTC apt-upgrade reboot — confirmed via `lumen_state.py current ptah` and a live `fleet-exec run ptah 'uptime'` SSH timeout on both LAN and tailnet addresses. No remote session exists to run the requested commands from.

## How
- Re-verified ptah's reachability before trusting the stale memory note.
- Logged the finding as ISSUE-LOG row 1354 (`/home/lumen/kb/ISSUE-LOG.md`) rather than editing `claude_memory` directly (memory consolidation stays proposal-only).
- Wrote `reports/wq3926_ptah_ncs2vision_watch.md` with method, findings, and a verdict.
- Queued a physical check/power-cycle of ptah (no smart plug assigned, so no remote actor can do this) as the one legitimate physical-task exception to full autonomy, instead of blocking the ticket on it.

## Testing
Documentation-only change; no code path affected. Verified the cited commands' actual output (`lumen_state.py`, `fleet-exec run ptah 'uptime'`) rather than asserting from memory.

## Risk / rollback
None — adds a report and a knowledge-base log row. No container restart was performed or is possible (host unreachable). Revert is a plain file revert if needed.

Refs: wq#3926

🤖 Generated with [Claude Code](https://claude.com/claude-code)
