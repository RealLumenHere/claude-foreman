# wq#4552 — dbhub fleet-state source over minerva.db (sqlite, read-only)

wq#4515 landed `fleet-memory`: @bytebase/dbhub 1.5.0 as a user-scope stdio MCP server over the
Postgres `fleet_memory` DB (role `mcp_ro`). That DB has been frozen since 2026-10-08 and is pending
collapse (wq#4539). The store agents actually need mid-session is the sqlite file
`/home/lumen/minerva/minerva.db` — `state_facts` (bi-temporal, ~1.2k open facts), `archival_notes`,
`memory_audit`.

## What changed (code, landed on this branch)

`ops/wq4514-mcp-install-batch/install.sh` gained a second dbhub registration, `fleet-state`, sharing
the same pinned `@bytebase/dbhub` 1.5.0 install `fleet-memory` uses:
- `emit_toml_fleet_state` / `emit_launcher_fleet_state` generate `fleet-state.toml` and
  `launch-fleet-state.sh` under the same `$ROOT/dbhub/1.5.0/` directory as `fleet-memory`'s
  `launch.sh`.
- `install_fleet_state` / `verify_fleet_state` wire it into the same `install.sh [TOOL...]` /
  `install.sh --verify` contract as the other four servers.
- `ops/wq4514-mcp-install-batch/smoke_fleet_state.py` is the acceptance test (see RUNBOOK.md).

**Read-only mechanism — this is the part that needed verifying, not assuming.** The ticket's
starting guess was dbhub's env-var `DSN=... READONLY=true` scheme (what `fleet-memory`'s launcher
uses for Postgres). Tested directly against dbhub 1.5.0 + sqlite: a `DELETE FROM state_facts WHERE
1=0` went through with that config — the env-var path does not populate the config value the
sqlite connector's read-only-open and keyword classifier actually read. Switching to a TOML config
with `[[tools]] name="execute_sql" readonly=true` does enforce it: the same DELETE comes back
`READONLY_VIOLATION` ("Only select, with, explain, pragma allowed"). This is the engine-level fix
landed in dbhub 0.22.6 for CVE-2026-61788 (`@bytebase/dbhub`'s read-only mode did not prevent
writes pre-0.22.6) — 1.5.0 is after that fix, but only the TOML path exercises it for sqlite; the
env-var path apparently never populates the same config. `fleet-state.toml` is therefore TOML, not
env vars, unlike `fleet-memory`.

Verified live (this ticket, 2026-10-23), through the real registered guard-wrapped launcher:
`SELECT count(*) FROM state_facts WHERE valid_to IS NULL` ~1244 open facts; `SELECT
subject,predicate,value ... WHERE subject='lumen'` returns real rows; INSERT/UPDATE/DELETE/CREATE
TABLE/`PRAGMA query_only=0` are all refused with `READONLY_VIOLATION`; `minerva.db` is WAL
(`sqlite3 "file:/home/lumen/minerva/minerva.db?mode=ro" "PRAGMA journal_mode"` → `wal`), so this
read-only reader cannot block `lumen_state.py`'s writes. No network listener, no mcpw/CF route —
stdio only, per the MCP freeze. Full run: `ops/wq4514-mcp-install-batch/RUNBOOK.md`.

## What is a staged diff, not a landed edit

`context/fleet_tools.yaml` and `/home/lumen/ORCHESTRATOR-HANDOFF.md` are outside this repo (they
live under `/home/lumen/minerva/context/` and `/home/lumen` directly). `guard.sh` refuses
Write/Edit inside those primary trees from a worker sandbox — confirmed live on this ticket:

```
guard.sh: BLOCKED — Write/Edit inside PRIMARY tree /home/lumen/minerva denied (wq#1719/wq#900) —
primary repo roots are read-only for workers; ... for anything else make the change in your own
worktree and land it with lumen-pr
```

— the same boundary wq#2607/wq#2613/wq#3911 hit on `fleet_tools.yaml` before this ticket. Both
diffs in this directory are staged, content-anchored (not line-anchored — this sandbox cannot read
the live files to get real line numbers), ready for hand-apply or fold-in by whoever maintains those
two files:

- `wq4552_fleet_tools.diff` — adds a `fleet-state (MCP)` entry under `memory_state.tools` in
  `context/fleet_tools.yaml`, alongside the existing `lumen_state.py` entry.
- `wq4552_orchestrator_handoff.diff` — adds a `fleet-state` paragraph to s9 (MCP servers) of
  `ORCHESTRATOR-HANDOFF.md`.

Hand-apply (adjust `-p1`/paths to taste; both hunks are small enough to apply by hand if `patch`
fuzzes on the lack of real line numbers):

```
patch -p1 -d /home/lumen/minerva < ops/wq4552-dbhub-add-fleet-state/wq4552_fleet_tools.diff
patch -p1 -d /home/lumen        < ops/wq4552-dbhub-add-fleet-state/wq4552_orchestrator_handoff.diff
```
