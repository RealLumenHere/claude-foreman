# wq#4945: cross-server tool-name collisions (audit F5)

Each `lumen_mcp_guard` instance sees one server, so its `--pins` check cannot notice a tool name
that another server also exposes. Claude Code calls tools as `mcp__<server>__<tool>`, so a shared
name cannot misroute there. A flat-namespace client resolves it to whichever server registered
last, and a new server could take over an existing name the same way.

`mcp_pin_check.py collisions` reads security-gate pin files (`servers[SERVER@SURFACE].tools`),
takes each server's agent-visible surface (`@guarded`, else `@unguarded`, never `@backend`), and
fails on any tool name on two or more servers. Names are compared case-insensitively, with `-`
and `.` read as `_`. A collision passes only if `accepted-collisions.json` lists that name with
exactly that server set and a reason, so a third server joining an accepted name fails again.

Run it after any pin regeneration or new MCP registration:

```
ssh lumen@100.117.66.102 'python3 -I /home/lumen/foreman/ops/wq4945-mcp-name-collisions/mcp_pin_check.py collisions /home/lumen/docs/security/mcp-tool-pins-2026-11.json /home/lumen/foreman/ops/wq4946-ghidra-mcp-guard/ghidra-pins.json --accept /home/lumen/foreman/ops/wq4945-mcp-name-collisions/accepted-collisions.json'
```

Exit 0: nothing unreviewed. Exit 1: an `UNREVIEWED` line names the collision. Exit 2: bad input.
Later files override earlier ones per `SERVER@SURFACE`, so a guard's own pin file goes after the
baseline. `STALE_ACCEPT` means an accepted entry no longer collides; delete it.

Today (2026-11-13): the baseline alone shows the 10 names from the audit. With ghidra-lumen's
guarded pins layered on, `list_instances` drops out (the wq#4946 allowlist hides it), leaving 9,
all accepted: `execute_sql`, `search_objects` (fleet-memory and fleet-state, two dbhub instances)
and 7 shared by ghidra-lumen and rea.

`fixtures/mcp-tool-pins-2026-11.json` is a byte copy of the 2026-11-12 baseline (sha256
`b1cad9a55c55564374d79bd62e3b44aba6d01fc546c273d9e1a402ae5698fddd`), so the tests do not
depend on the live file. Tests: `python3 -m pytest -q test_mcp_pin_check.py`.

Not covered: servers with no snapshot (serial-maksoor, rea-maksoor, agentvcs, director,
ghidra-mcp; audit F8, wq#4948) and anything registered after the baseline until it is
snapshotted.
