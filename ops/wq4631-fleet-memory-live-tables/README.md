# wq#4631 — fleet-memory table list in fleet_tools.yaml

wq#4554 (memory phase 2) renamed `fleet_facts`, `fleet_devices`, `fleet_decisions` and
`agent_episodes` to `retired_*`. The live tables in `fleet_memory` are `memory_events`,
`memory_sources` and `recipes`. The `fleet-memory` entry in
`/home/lumen/minerva/context/fleet_tools.yaml` still listed the old names.

The probe side of the fix (`ops/wq4514-mcp-install-batch/install.sh` and
`smoke_postgres.py` now target `memory_events`) lands in this PR. The yaml is gitignored
live config in a primary tree that BUILD workers can't edit, so it has to be hand-applied:

    patch -d /home/lumen/minerva -p1 < ops/wq4631-fleet-memory-live-tables/wq4631_fleet_tools.diff
    bash ops/wq4631-fleet-memory-live-tables/install.sh --verify   # expect VERIFY OK

No DB or grant changes. RUNBOOK.md grant note unchanged.
