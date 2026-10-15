# LIMPET — design decisions log

A running record of load-bearing choices in the Limpet build, newest first. Each entry
says what was decided, why, and where the code or spec lives, so a later ticket can see the
reasoning without re-deriving it. (This file did not exist before wq#3934 —
`limpet/tool-catalog.json` noted its absence; it is created here and entries for earlier
subsystems can be backfilled as those tickets touch it.)

## wq#3935 — ewf absorption: Rockchip MCP tool + device-profile merge

**D1 — ewf is retired as a standalone app; its function moves into Limpet.**
The e-waste-flasher Tauri app (`/home/lumen/build/ewf/`, rebuild history in
`docs/wq3244_ewf_rebuild_audit.md`, backend research in
`docs/wq3253_ewf_amlogic_allwinner_backends.md`) duplicated two things Limpet
already owns: a USB device-detection layer (HAL + target device DB, wq#3809 /
wq#3940) and a tool-dispatch layer (MCP meta-server, wq#3808). Maintaining a
second GUI app around the same rkdeveloptool calls meant two VID:PID tables
and two command surfaces drifting apart. ewf's two real assets — its Rockchip
device-profile knowledge and its backup/flash workflow — are absorbed as:

- per-SoC profiles in `limpet/target-devices.d/rockchip.toml`
  (`recommended_commands`, `partition_layout` + `[partition_layouts.*]`),
- MCP wrappers in `limpet/tools/rockchip/rockchip-mcp.toml`
  (`list_devices`, `read_partition_table`, `read_flash`, `write_flash`),
- a plugin manifest `limpet/tools/rockchip/limpet-plugin.toml` so the
  registry (wq#3810) tracks/health-checks it like any other tool plugin.

**D2 — wrappers are authored in TOML, rendered to the meta-server's JSON.**
The MCP meta-server's registry manifest is JSON and stays JSON — its
hot-reload, validation, and packaging (`packaging/limpet-mcp-meta`) are built
and tested around it (wq#3808/wq#3821), and teaching it a second manifest
format would put parsing risk in the serving path for zero serving benefit.
The Rockchip tool is instead *authored* as `[[wrappers]]` TOML (consistent
with every other Limpet drop-in: plugin manifests, target-device catalogs)
and `limpet/tools/rockchip/mcp_wrappers.py render` emits the `cli_tools` JSON
array verbatim in the shape `registry.py` parses. Validation happens at
render time, where failing is cheap: every `{placeholder}` must be a whole
argv element (the cli_wrapper substitution contract) and must be declared +
required in the wrapper's `args_schema`.

**D3 — destructive wrappers render disabled by default.**
`write_flash` overwrites device flash irreversibly, and the meta-server has no
operator-confirm step — any connected agent can call any enabled tool. Same
posture as the target-DB rule that below-high confidence must not gate an
irreversible action (wq#3940 header): the rendered `write_flash` entry carries
`"enabled": false` plus a note; turning it on is a deliberate, auditable
manifest edit made after a verified backup exists. The read-only verbs
(`list_devices`, `read_partition_table`, `read_flash`) render enabled.
MaskROM loader-chainloading (`rkdeveloptool db`) is not wrapped at all: it
needs a per-SoC loader blob, and the wrong blob wedges the BootROM session —
it stays an operator step, spelled out per-SoC in `recommended_commands`.

**D4 — device profiles extend [[device]]; partition layouts are shared tables.**
ewf kept a separate device-profile DB. Limpet instead extends the existing
`[[device]]` entries (wq#3940) with two optional fields —
`recommended_commands`, `partition_layout` — rather than standing up a second
catalog keyed by the same VID:PIDs, which would recreate exactly the
drift-between-two-tables problem D1 retires. Partition layouts are shared
`[partition_layouts.<name>]` tables referenced by name, because Rockchip
generations cluster on a few layouts (fixed-offset GPT convention vs. legacy
parameter-file) and inlining per-entry copies invites drift;
`load_partition_layouts()` fails loud on a name collision across files. The
fields are optional with empty defaults so the other eleven vendor catalogs
load unchanged until each gets its own profiling pass. Layout tables describe
the vendor-standard image convention; live `rkdeveloptool ppt` output always
outranks them for an attached device.

## wq#3934 — reporting/export system

**Decision.** `limpet-workspace export` renders a workspace to **Markdown (default), HTML,
and JSON** from a single Investigation Record (IR) assembled once from committed workspace
state. Reports are written to `<workspace>/reports/<report-id>/`. Custom formats are Jinja2
templates resolved from the workspace, then `/etc/limpet/report-templates`, then packaged
built-ins. The agent chat `/export` command drives the same core. Spec:
`docs/reporting-spec.md` (Part II). Code: `limpet/workspace_report.py`,
`limpet/workspace_cli.py`, `limpet/report-templates/`.

**Why these calls:**

- **One IR, many views.** JSON *is* the validated IR; Markdown and HTML are Jinja2 views of
  it. Formats rendered from one source cannot disagree, and the JSON feeds downstream tooling
  verbatim. (Part I §2.)
- **A report is derived, never authored.** Every field is read from `manifest.json`,
  `evidence/findings/*.json`, and `workspace.git`; the generator never takes free-text
  findings on the command line. This is what lets a report be re-derived byte-for-byte at a
  pinned commit (`--source-date none`) and so count as evidence. (Part I §0.2–0.3.)
- **Masking once, on the IR.** Tool output can carry baked-in secrets; the IR is passed
  through a credential mask before any renderer sees it, so every format redacts identically
  (fleet instinct 4). `--no-redact` exists for a sealed internal copy but stamps
  `report_meta.redacted=false` so an unredacted report is self-identifying.
- **HTML is self-contained and escapes everything.** CSS inlined, no `<script>`, no remote
  resource, forced Jinja2 `autoescape` with the `safe` filter removed — a malicious target
  string (a filename, a version banner) cannot become XSS or an exfil beacon in a report that
  gets emailed around. The appliance is headless, so HTML opens offline with no display server.

**Scope held back from the Part I (wq#3905) design, on purpose.** wq#3934 ships the
operator-facing core; it does **not** build PDF/WeasyPrint output, `--sign`/`--bundle`
evidence bundles, the `verify` subcommand, or the MCP `generate_report` router tool. The
operator (not a paying client) is the reader, and those features add a browser/GPU or signing
key surface that the three text formats do not need. Part I remains the target for them;
`--format` rejects anything but `md|html|json|all` until then.

**Divergences from Part I worth flagging** (Part I is the north star; the build honoured the
wq#3934 ticket where they differed): reports land in `<workspace>/reports/` (ticket), not
`evidence/reports/` (Part I §1); default format is `md` (ticket), not `all`; the site template
dir is `/etc/limpet/report-templates` (ticket), not `report-templates.d` (Part I §5).

**Packaging.** Added to `limpet-tools`: the `limpet-workspace` bin shim, `workspace_cli.py`
+ `workspace_report.py` + the `report-templates/` tree under `/usr/lib/limpet/tools/`, an
empty `/etc/limpet/report-templates` site drop-in, and `python3-jinja2` + `git` as
dependencies. (`packaging/limpet-tools/debian/{rules,install,control}`.)
