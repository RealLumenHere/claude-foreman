# wq#4788 — retire neith/khnum from fleet_tools.yaml

`/home/lumen/minerva/context/fleet_tools.yaml` (primary tree, not writable from this
worktree's sandbox — guard.sh blocks Edit under `/home/lumen/minerva`) still listed neith
(Commander primary) and khnum (Commander mirror) as SERVING under group `p1_commander`,
and the `commander_mcp` invoke text said "neith/khnum standbys". Both are
`lifecycle=retired` / `role=RETIRED` in lumen_state (since 2026-10-27, Frank-stated,
confirmed via `lumen_state.py current neith` / `current khnum`), and
`claude_memory.md` hand-prose already carries the correct 2026-10-27 STANDING ORDER.
Only `fleet_tools.yaml` — rendered to `FLEET_TOOLS.md` and injected by every
`claude_context.sh --core` boot — was stale.

`wq4788_fleet_tools.diff` is the exact patch (3 hunks): fixes the boot-note's
Commander/standby line, fixes `commander_mcp`'s Commander MCP invoke text, and removes
the `p1_commander` group entirely (no replacement entry — Commander is horus-only, no
live standby).

## Apply (needs a worker with /home/lumen/minerva write access, outside this sandbox)

```
cp /home/lumen/minerva/context/fleet_tools.yaml /home/lumen/minerva/context/fleet_tools.yaml.bak.wq4788
cd /home/lumen/minerva && patch -p1 < /home/lumen/foreman/ops/wq4788-fleet-tools-neith-khnum-retired/wq4788_fleet_tools.diff
/home/lumen/bin/fleet-tools-render
/home/lumen/bin/fleet-tools-render --boot
```

## CLASS (registry/lifecycle drift)

This is the second fleet_tools.yaml staleness ticket of this shape (first was wq#4712's
disk-layout note, which flagged this exact file as a known-unchecked residual). Registry
entries in fleet_tools.yaml carry no lifecycle link to lumen_state, so a subject's
`lifecycle=retired`/`decommissioned` never invalidates an entry naming it.
`fleet-tools-render` already parses each entry's tool names/descriptions; it could grep
each entry's prose for a bare subject name, look it up via `lumen_state.py current`, and
warn (non-fatal) when a named subject's lifecycle is retired/decommissioned. Left as a
follow-up, not implemented here — scope of this ticket is the stale entries themselves.
