## What

wq#3935 — ewf (e-waste-flasher) retires as a standalone Tauri app; its Rockchip surface is absorbed into Limpet:

1. **Rockchip MCP tool** — `limpet/tools/rockchip/rockchip-mcp.toml`, a `[[wrappers]]` TOML manifest wrapping rkdeveloptool with the four ticketed verbs (`list_devices`, `read_partition_table`, `read_flash`, `write_flash`). `limpet/tools/rockchip/mcp_wrappers.py` validates it and renders it into the wq#3808 MCP meta-server's `cli_tools` JSON. The destructive `write_flash` renders `enabled=false` by default.
2. **ewf device-profile DB merged into HAL target detection** — all 17 `limpet/target-devices.d/rockchip.toml` entries (every MaskROM/Loader VID:PID) gain `recommended_commands` (operator playbook; MaskROM playbooks start with the loader chainload) and `partition_layout`, resolving against new shared `[partition_layouts.*]` tables (`rk-gpt-standard`, `rk-legacy-parameter`). `limpet/target_devices.py` grows the optional `TargetDevice` fields and `load_partition_layouts()`.
3. **Plugin manifest** — `limpet/tools/rockchip/limpet-plugin.toml`, valid against the wq#3810 `limpet/manifest.py` schema, health-checked via `rkdeveloptool ver`.
4. **Design records** — `limpet/DESIGN-DECISIONS.md` D1–D4.

## Why

ewf duplicated two things Limpet already owns — USB device detection (HAL + wq#3940 target DB) and tool dispatch (wq#3808 MCP meta-server) — so two VID:PID tables and two rkdeveloptool command surfaces were drifting apart. Full rationale in `limpet/DESIGN-DECISIONS.md` D1.

## How

Wrappers are *authored* in TOML (consistent with every other Limpet drop-in) and *rendered* to the meta-server's existing JSON manifest format rather than teaching the serving path a second parser (D2). Validation enforces the cli_wrapper substitution contract: every `{placeholder}` is a whole argv element and declared + required in `args_schema`. The profile merge extends existing `[[device]]` entries with optional fields instead of standing up a second catalog keyed by the same VID:PIDs (D4); layouts are shared named tables, and `load_partition_layouts()` fails loud on cross-file name collisions. Other vendor catalogs load unchanged via empty defaults. MaskROM loader chainloading (`rkdeveloptool db`) is deliberately not wrapped — wrong blob wedges the BootROM session; it stays an operator step in `recommended_commands` (D3).

## Testing

`python3 -m pytest limpet/tools/rockchip/test_rockchip_mcp.py limpet/test_target_devices.py -q` → 22 passed. Full `pytest limpet` minus six pre-existing collection errors (also present on main: `limpet/registry/` dir shadows `registry.py` for those modules) → 67 passed. The rendered JSON is parsed by the real `ops/wq3808-limpet-mcp-meta-server-a1/registry.py` in-test; `limpet-plugin.toml` is parsed by the real `limpet/manifest.py`. Mutation probes per wq#2630: undeclared placeholder, embedded placeholder, missing `destructive` flag, and negative timeout are each rejected; a qualcomm (unprofiled-population) entry loads with empty profile defaults.

## Risk / rollback

Data + new modules only; no existing runtime path changes behavior (`TargetDevice` fields are optional with defaults, udev rules render byte-identical — drift test still passes). Nothing deploys automatically: the rendered `cli_tools` entries only reach a meta-server when merged into its deployed manifest, and the plugin only registers when its drop-in lands in `/etc/limpet/plugins.d/`. Rollback is `git revert` of this commit.

Notes: `/home/lumen/build/ewf/` was outside this session's allowed directories; the profile concept was reconstructed from the in-repo ewf record (`docs/wq3244_ewf_rebuild_audit.md`, `docs/wq3253_ewf_amlogic_allwinner_backends.md`) and the wq#3923/wq#3940 research corpus. fleet-search (web + github) was down this session — logged as ISSUE-LOG row 1332.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
