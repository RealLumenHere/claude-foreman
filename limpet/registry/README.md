# LIMPET tool-registry manifest (wq#3807)

A machine-readable catalog of the RE tools installed on the Limpet node, so an
agent can decide *before trying* whether it has a tool for a job — what it can
do, what formats it eats and emits, which architectures and hardware it needs,
whether it exposes an MCP endpoint, and how to prove it still runs.

## Model

Same lineage as `sysctl.d` / `udev/rules.d`: **one manifest file per tool** in a
drop-in directory, plus a rebuilt unified catalog.

```
/etc/limpet/tools.d/            drop-in dir — one <tool>.yaml per installed tool
        ├── ghidra.yaml
        ├── binwalk.yaml
        └── radare2.yaml
/etc/limpet/tools.json          unified, machine-readable catalog (rebuilt)
```

- **Tools self-register on install** — a tool's install script writes its own
  `<name>.yaml` into `tools.d/` via `limpet-registry register`.
- **A watcher rebuilds the unified manifest** — the `limpet-registry.path`
  systemd unit fires on any change under `tools.d/` and runs `limpet-registry
  build`, which validates every drop-in and writes `tools.json` atomically.
- **The agent reads `tools.json`** — including a `capabilities` index
  (`{capability: [tool names]}`) so "which tool can decompile a Mach-O" is one
  lookup, not a scan.

## Manifest schema (v1)

Required: `name`, `path`, `capabilities`, `health_check`. Optional: `version`,
`description`, `category`, `input_formats`, `output_formats`, `architectures`,
`hardware_requirements`, `mcp_endpoint`. See `schema.py` and the seed manifests
in `tools.d/` for the exact shape; `binwalk.yaml` is the minimal reference.

## CLI

```
python3 -m limpet.registry.cli build        # rebuild tools.json from tools.d
python3 -m limpet.registry.cli validate     # check every drop-in, nonzero on error
python3 -m limpet.registry.cli list --capability decompilation
python3 -m limpet.registry.cli register tool.yaml
python3 -m limpet.registry.cli unregister ghidra
python3 -m limpet.registry.cli import --catalog /home/lumen/projects/limpet/tool-catalog.json
python3 -m limpet.registry.cli watch         # polling fallback (no systemd)
python3 -m limpet.registry.cli health        # run each tool's health_check
```

## Seeding the 147-tool catalog

The hand-maintained `tool-catalog.json` predates self-registration. `import`
converts each entry to a validated per-tool manifest, reading each field through
an alias list (`binary`→`path`, `caps`→`capabilities`, …) so the catalog's own
key names need not match the schema. An entry that cannot be made valid (no
runnable path to build a health check from) is reported and skipped, never
guessed in. After import, each tool re-registers itself on its next install.

## Install

```
sudo install -d /etc/limpet/tools.d
sudo cp systemd/limpet-registry.{path,service} /etc/systemd/system/
sudo systemctl enable --now limpet-registry.path
```

## Tests

`pytest limpet/registry/tests/` — schema validation, drop-in load (malformed and
duplicate handling), index build, rebuild idempotency, register round-trip,
catalog import via alias mapping, watcher signature, and health runner. Driven
by the committed fixture `tests/fixtures/tool-catalog.sample.json`.
