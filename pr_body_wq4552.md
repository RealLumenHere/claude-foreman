## What
Adds a second dbhub 1.5.0 MCP registration, `fleet-state`, over
`/home/lumen/minerva/minerva.db` (sqlite, read-only) alongside the existing `fleet-memory`
registration (wq#4515, Postgres). Stages doc updates for `fleet_tools.yaml` and
`ORCHESTRATOR-HANDOFF.md` as diffs (both live outside this repo, in primary trees `guard.sh`
blocks a worker sandbox from editing directly).

## Why
`fleet-memory` points at a Postgres `fleet_memory` snapshot frozen since 2026-10-08, pending
collapse (wq#4539). Agents need the LIVE fleet memory mid-session — `state_facts` (bi-temporal,
~1.2k open facts), `archival_notes`, `memory_audit` — which lives in sqlite, not that Postgres
DB.

## How
- `ops/wq4514-mcp-install-batch/install.sh`: new `fleet_state` tool (`emit_toml_fleet_state`,
  `emit_launcher_fleet_state`, `install_fleet_state`, `verify_fleet_state`), sharing the existing
  dbhub 1.5.0 npm install `fleet-memory` uses. Registered user-scope stdio, behind
  `lumen_mcp_guard`, same allowlist (`execute_sql`, `search_objects`) as `fleet-memory`.
- Read-only comes from a TOML `[[tools]] readonly=true` config (`fleet-state.toml`), not the
  env-var `DSN=... READONLY=true` scheme `fleet-memory` uses for Postgres: verified empirically
  that the env-var path does not block writes on dbhub's sqlite connector in 1.5.0 (a
  `DELETE ... WHERE 1=0` went through), while TOML `readonly=true` does (`READONLY_VIOLATION`) —
  this is the dbhub 0.22.6 engine-level fix for CVE-2026-61788, which only the TOML path exercises
  for sqlite.
- `ops/wq4552-dbhub-add-fleet-state/wq4552_fleet_tools.diff` and `wq4552_orchestrator_handoff.diff`
  are staged, content-anchored diffs for `fleet_tools.yaml` (`memory_state`) and
  `ORCHESTRATOR-HANDOFF.md` (s9) — see that directory's README for the hand-apply commands and why
  they're staged rather than landed.

## Testing
- `install.sh fleet_state` then `install.sh --verify fleet_state` → `VERIFY OK` (installs,
  registers, and makes a real read-only `execute_sql` call through launcher → guard → dbhub).
- `claude mcp get fleet-state` → User config (all projects), stdio, Connected.
- `ops/wq4514-mcp-install-batch/smoke_fleet_state.py` through the real registered launcher →
  `RESULT: ALL PASS`: `tools/list` exposes exactly `execute_sql`/`search_objects`; `SELECT
  count(*) FROM state_facts WHERE valid_to IS NULL` returns a real count (~1244, close to the
  ticket's ~1273 — live table, drifts); `SELECT subject,predicate,value ... WHERE subject='lumen'`
  returns real rows; INSERT/UPDATE/DELETE/CREATE TABLE/`PRAGMA query_only=0` are all refused with
  `READONLY_VIOLATION`.
- `sqlite3 "file:/home/lumen/minerva/minerva.db?mode=ro" "PRAGMA journal_mode"` → `wal`, so this
  read-only reader cannot block `lumen_state.py`'s concurrent writes.
- `test_lumen_mcp_guard.py` regression: 17 passed (nothing broke for the other four servers).

## Risk/rollback
No network listener, no mcpw/CF route opened (freeze respected) — stdio MCP only. No secret or
credential involved (sqlite has no role system; read-only is enforced at the SQL layer per above).
Rollback: `claude mcp remove -s user fleet-state` (no role/secret to revoke). Worst case if the
TOML read-only enforcement were ever found to regress: `fleet-state` can only reach
`execute_sql`/`search_objects` on one already-world-readable sqlite file on the same host, through
`lumen_mcp_guard`'s fail-closed allowlist — no escalation path beyond that file.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
