# wq#4913 — maksoor rea-guard: pin version, block self-update, route through guard

## Finding

Maksoor's rea install is 6.4.0 (Lumen pins 6.5.0) and runs unguarded — no
self-update block, no PROVENANCE.json, no version pin. wq#4006's RUNBOOK
explicitly flagged this as needing its own ticket: "apply the equivalent
rea-guard + pin on Maksoor separately — that host is outside this bundle's
reach too and needs its own ticket to cover rea-maksoor's remote side
symmetrically."

Additionally, maksoor was unreachable from Lumen (SSH :22 and serial-mcp
:8400 timeout) as of 2026-11-05. This sandbox cannot perform network
probes (ssh/ping/tailscale blocked by worker allowlist). Connectivity
must be restored before this bundle can deploy.

## What this bundle does

1. **Guard script** (`rea-guard`): identical logic to Lumen's wq#4006
   guard but with maksoor's npm-global path. Blocks `update`,
   `self-update`, `upgrade` subcommands.

2. **Version upgrade**: upgrades rea-agents on maksoor from 6.4.0 to
   6.5.0 (the Lumen-pinned version).

3. **Provenance**: generates `PROVENANCE.json` on maksoor at
   `/home/maksoor/re/PROVENANCE.json` using the same generator as
   wq#4006.

4. **MCP registration update**: patches Lumen's project-scope
   `~/re/.mcp.json` so `rea-maksoor` calls
   `/home/maksoor/re/bin/rea-guard mcp` instead of the bare
   `/home/maksoor/.npm-global/bin/rea mcp`. The template
   (`rea.mcp.json.project`) is also updated for future deploys.

## Deploy (privileged, not worker-safe)

Prerequisite: maksoor must be SSH-reachable from Lumen.

```
bash ops/wq4913-maksoor-rea-guard/install.sh
```

Override env vars for non-default targets:
- `WQ4913_MAKSOOR_HOST` (default: `maksoor@100.81.204.74`)
- `WQ4913_MAKSOOR_RE_DIR` (default: `/home/maksoor/re`)
- `WQ4913_LUMEN_PROJECT_DIR` (default: `~/re`)
- `WQ4913_REA_VERSION` (default: `6.5.0`)

## Verify

```
bash ops/wq4913-maksoor-rea-guard/install.sh --verify
```

## Connectivity triage (for the unreachable finding)

If maksoor is still unreachable at deploy time:
1. `ping 100.81.204.74` (Tailscale) and `ping 192.168.8.140` (LAN)
2. `tailscale status | grep maksoor`
3. Physical check: power on, lid open (GNOME lid-close was fixed but
   could regress), wifi connected (wlp2s0, no wired NIC)
4. If Tailscale is down but LAN works: `ssh maksoor@192.168.8.140`
   then `sudo tailscale up` on maksoor

## Revert

Remote: `rm -rf /home/maksoor/re/bin/rea-guard /home/maksoor/re/PROVENANCE.json`
Lumen: restore `~/re/.mcp.json.pre-wq4913-*` backup.
