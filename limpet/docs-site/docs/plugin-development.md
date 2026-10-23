# Plugin Development

Limpet's tools, hardware definitions, and data sources are **plugins**. A plugin is a
`limpet-plugin.toml` manifest — drop one into `/etc/limpet/plugins.d/` and the
registry daemon validates it, resolves its dependencies and conflicts, health-checks
it, and publishes a unified view every other component reads. Most tool plugins also
ship a thin **MCP wrapper** so the agent can call the tool through the meta-server.

## Write a plugin in 60 seconds

Create `my-thing.toml` — the filename stem must equal the plugin `name`:

```toml
[plugin]
name = "my-thing"
version = "1.0.0"
kind = "tool"          # or "hardware" / "data"
category = "fuzzer"    # see manifest.py CATEGORIES for the full enum
summary = "what it does"
conflicts = ["other-plugin-name"]   # optional: can't both be active

[dependencies]            # optional: other plugins it needs
some-dep = ">=2.0"        # missing or version-mismatched -> blocked

[optional_dependencies]   # optional: helps but never required
nice-to-have = "*"

[health]                  # optional: how it proves it's alive (exit 0 = healthy)
command = "my-thing --health"
interval_seconds = 60
timeout_seconds = 10
```

Validate, install, and inspect it:

```sh
python3 limpet/cli.py validate my-thing.toml
cp my-thing.toml /etc/limpet/plugins.d/
python3 limpet/cli.py list                 # effective state of every plugin
python3 limpet/cli.py list --category sdr  # filter by category
python3 limpet/cli.py disable my-thing     # toggle off without uninstalling
```

Share it as a git repo with the `.toml` at its root — see `limpet/examples/`.

## Adding an MCP-wrapped tool

A tool plugin that the agent should be able to call declares an `[mcp]` block and some
`[agent_hints]`. The first-party `flashrom` plugin
(`plugins/limpet-plugin-flashrom/limpet-plugin.toml`) is a complete worked example:

```toml
[plugin]
name = "flashrom"
version = "1.4.0"
manifest_version = 1
category = "flash-tools"
description = "SPI flash read/write/erase/verify via flashrom"
license = "GPL-2.0-only"
homepage = "https://flashrom.org"

[capabilities]
flash_read = true
flash_write = true
architectures = ["x86", "x86_64", "ARM", "ARM64"]
input_formats = ["bin", "rom"]
output_formats = ["bin", "rom"]

[health]
check = "flashrom --version 2>&1 | grep -q 'flashrom'"
interval = "1h"
timeout = "5s"

[mcp]
enabled = true
server = "limpet-flashrom-mcp"
transport = "stdio"
command = "python3"
args = ["/opt/limpet/plugins/flashrom/mcp/server.py"]

[mcp.tools_energize]
# Safe operations need no energize gate.
probe = false
read = false
# Destructive operations MUST be gated.
write = true
erase = true

[agent_hints]
summary = "SPI flash programmer for BIOS/firmware/EEPROM chips."
when_to_use = "Use when the HAL detects a flash programmer, or to dump/flash a SPI NOR chip. Always read+verify before any write."
when_not_to_use = "Not for NAND, eMMC, or UFS. Not for in-circuit bootloader flashing."
complements = ["binwalk", "unblob", "ghidra"]
```

### `[mcp.tools_energize]` — gate the dangerous calls

Every MCP tool the plugin exposes is listed here as `true` (destructive — requires an
explicit *energize* confirm before it runs) or `false` (safe — runs freely). Read,
probe, and verify are typically `false`; write and erase are `true`. This is how the
trust gate knows a `flashrom write` can brick a board and a `flashrom read` cannot.

### `[agent_hints]` — teach the agent when to reach for it

These fields are what the agent reads to decide whether your tool fits the task:
`summary`, `when_to_use`, `when_not_to_use`, `complements`, `supersedes`, and
`workflow_position` (`entry` / `middle` / `exit`). Good hints are the difference
between the agent picking your tool at the right moment and never picking it at all.

## Trust tiers

Plugins carry a trust tier (`core`, `community`, …). The meta-server refuses a
`community`-tier call and returns a structured "promotion required" error; the agent
may propose a promotion but cannot self-promote. Set a ceiling when you add a registry
index so an untrusted source can never claim `core`:

```sh
python3 limpet/limpet_tools.py index add <git-url> --name canonical --ceiling core
```

## How the Tool Reference stays honest

The [Tool Reference](tool-reference.md) is generated from the shipped
`tool-catalog.json` **and** every `plugins/*/limpet-plugin.toml`, by
`limpet/docs-site/gen_tool_reference.py`. When you add a plugin, regenerate it:

```sh
python3 limpet/docs-site/gen_tool_reference.py
```

CI runs the same script with `--check` and fails if the committed page is stale, so a
new plugin cannot ship without its documentation.
