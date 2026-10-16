# LIMPET canonical names (wq#4080)

The specs used up to three names for each daemon, path and gate. This file
records the name that wins. The rule: whatever foreman `packaging/` ships is
correct, and specs change to match it. Code changes only where a spec name had
leaked into it. Source of the conflict list:
`/home/lumen/projects/limpet/docs/audit-2026-10-16-limpet-iso.md` §1 "Conflicts
between project docs" (agent limpet-iso-6302d0).

## Canonical table

| Thing | Canonical (as-built) | Retired spec names | Evidence |
|---|---|---|---|
| HAL daemon binary / package | `limpet-hald` | `limpet-hal` (as a daemon or unit name) | `packaging/limpet-hald/debian/control:8`, `packaging/limpet-hald/debian/install` |
| HAL systemd unit | `limpet-hald.service` (spec'd; not yet shipped, see gaps) | `limpet-hal.service` | none shipped |
| Registry daemon | `limpet-registry-daemon` (`/usr/bin`), unit `limpet-registry-daemon.service` | `limpet-registry`, `limpet-registryd`, `limpet-registry.service` | `packaging/limpet-tools/debian/limpet-registry-daemon.service:7`, `packaging/limpet-tools/debian/install` |
| MCP meta-server binary / package / unit | `limpet-mcp-meta`, `limpet-mcp-meta.service` | `limpet-meta-server` | `packaging/limpet-mcp-meta/debian/control:8`, `packaging/limpet-mcp-meta/debian/limpet-mcp-meta.service:8` |
| MCP `serverInfo.name` / `clientInfo.name` | `limpet-mcp-meta` | `limpet-meta-server` | `ops/wq3808-limpet-mcp-meta-server-a1/meta_server.py:241`, `stdio_mcp_client.py:92` (renamed in wq#4080) |
| Resolved registry file | `/var/lib/limpet/unified.json` | `/var/lib/limpet/registry.json`, `/var/lib/limpet/tool-registry.json` | `limpet/registry.py:32`, `limpet/registry.py:77` |
| Gate (doctrine name) | energize-confirm gate | none; this name stays | hal-spec.md §9, `docs/limpet_readme.md:68` |
| Gate (agent-facing MCP tool) | `limpet_confirm_destructive` | none; this name stays | mcp-meta-spec.md §6.3 |

Gate vs. tool: the two names are not rivals. "energize-confirm" is the gate,
`limpet_confirm_destructive` is the single MCP tool an agent calls to pass it
with a `confirmation_id`. Neither exists in code yet (no hit in `ops/wq3809-limpet-hal/`
or `ops/wq3808-limpet-mcp-meta-server-a1/`), so no code was renamed.

`limpet-hal-notify`, the udev relay helper in hal-spec.md §2, keeps its name.
It is a helper, not the daemon.

## Target-wizard MCP tools

target-wizard-spec.md §6.1/§8 defines `limpet_target_identify`,
`limpet_target_vectors`, `limpet_target_vulns`, `limpet_target_recommend`,
`limpet_vision_capture`, `limpet_vision_identify` and uses
`limpet_workspace_create`. They are now listed in mcp-meta-spec.md §7.3 as
meta-server tools. None is implemented yet.

## As-built gaps found while reconciling (not fixed here)

- `limpet-hald` ships no systemd unit. `packaging/limpet-hald/debian/` has only
  the udev reload in `postinst`, and there is no `/run/limpet/hal.sock` listener in
  `hal.py`. Specs still describe `limpet-hald.service` and the socket as the target.
- `limpet-mcp-meta` speaks stdio only and takes exactly one manifest argument
  (`meta_server.py:302`). The `--transport sse --port 3200` example in the
  READMEs and mcp-meta-spec.md §2.2 is spec, not as-built.
- There are two registries. The second is wq#3807's `limpet/registry/` (`limpet-registry.service`
  + `.path`, writes `/etc/limpet/tools.json`, `WorkingDirectory=/home/lumen/foreman`).
  It is unpackaged and dev-only. The packaged one, wq#3810's `limpet/registry.py` via
  `limpet-registry-daemon`, wins. `docs/plugin-spec.md:153` already marks the
  wq#3807 unit as "a different subsystem".

## Where the edits landed

- foreman (this PR): `docs/limpet_readme.md` (service and binary names),
  meta-server `serverInfo`/`clientInfo` + its two tests, this file.
- `/home/lumen/projects/limpet/` (not a git repo, so these edits have no history. Each rename is listed
  in the canonical table, and every hand-written addition is tagged `wq#4080`):
  `README.md`, `docs/hal-spec.md` (names + §9.4 item 8 gate/tool mapping),
  `docs/mcp-meta-spec.md` (names, unified.json, §6.3 gate mapping, §7.3 tool
  table), `docs/tool-registry-spec.md` (names, unified.json, split merged list
  item at §4.2 step 6/7), `docs/install-wizard-spec.md` (registry file),
  `docs/target-wizard-spec.md` (§1.2 gate/tool mapping).

The audit doc is evidence, so it keeps the old names.
