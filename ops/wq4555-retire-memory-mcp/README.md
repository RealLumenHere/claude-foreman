# wq#4555 — retire memory-mcp.service (wq#4539 phase 3)

Attended runbook. memory-mcp had 0 callers over the trailing 7 days (re-verified
2026-10-23: `journalctl -u memory-mcp --since "7 days ago"` → no entries; fleet
grep for `:3104` hits only staged ops copies and the wq#2751 listener test).
Post wq#4554 the fleet-memory MCP's `execute_sql` targets frozen PG tables, so
nothing a session should touch remains behind the port.

## Why attended

`systemctl disable` and `systemd-run` are on the worker deny-list by design.
A human-driven root session runs `apply.sh`; the disable itself goes through
detached `systemd-run` (instinct 5 pattern).

## Order

1. `sudo ./apply.sh precheck` — re-verify 0 callers, back up
   `/etc/systemd/system/memory-mcp.service.d/` to `/home/lumen/backups/wq4555-memory-mcp/`.
2. `sudo ./apply.sh retire` — detached-disable the unit, remove the wq#2768
   drop-in, confirm `:3104` refused, run the (inverted) wq#2751 live audit.
3. `./apply.sh mcp-entry` — as lumen: drop the `fleet-memory` entry from
   `~/.claude.json` (backs up first, idempotent).
4. Watch `journalctl -u memory-mcp` for **1 week**. Anything logs → `sudo ./apply.sh rollback`.
5. Quiet week → remove `/etc/systemd/system/memory-mcp.service` by hand (and the
   `.service.d/` dir); backups stay in `/home/lumen/backups/wq4555-memory-mcp/`.

## Repo-side changes landed with this runbook

- `ops/wq2751-listener-audit/test_wq2751.py` — the ":3104 still on 127.0.0.1"
  live check inverted to ":3104 refused" (a listener audit asserting a retired
  port is open would page forever). Rollback requires reverting that inversion.
- `ops/wq2768-access-guard/` — the memory-mcp half (staged drop-in + apply.sh
  loop/verify/restart) removed so a redeploy can't resurrect a guard for a
  disabled unit. chrome-mcp half untouched.
