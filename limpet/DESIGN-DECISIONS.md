# LIMPET — design decisions log

A running record of load-bearing choices in the Limpet build, newest first. Each entry
says what was decided, why, and where the code or spec lives, so a later ticket can see the
reasoning without re-deriving it. (This file did not exist before wq#3934 —
`limpet/tool-catalog.json` noted its absence; it is created here and entries for earlier
subsystems can be backfilled as those tickets touch it.)

## wq#3997 — adopt a published RE-agent benchmark as the eval instrument

**D1 — adopt AgentRE-Bench as the default instrument; keep the loader benchmark-agnostic.**
The live search (`scratch/research/limpet_live_search_2026-10-14.md` A1) surfaced three
published agentic-RE benchmarks: CrackMeBench (arXiv 2605.10597), AgentRE-Bench
(agentre-bench.ai), and a survey (arXiv 2604.14317). AgentRE-Bench is the default source
because its unit of evaluation — an agent that plans, drives tools, and produces graded
findings end to end — is exactly what the appliance ships; CrackMeBench scores single-binary
software-license reversing, a narrower, software-centric slice further from the firmware/
hardware emphasis the ticket's selection criterion names. The loader (`limpet/benchmark.py`
`load_suite`) is nonetheless benchmark-agnostic: it reads `[[task]]` tables from
`limpet/benchmark.d/*.toml`, each tagged with its source `benchmark` and `target_class`, so
which benchmark the appliance is measured against is a question of which drop-ins ship, not of
code — the one-schema-many-files posture of the target-device catalog (wq#3940). Rationale
and candidate comparison: `design/wq3997_limpet_re_benchmark.md`.

**D2 — the instrument is pure and offline-testable; the agent is an injected boundary.**
Limpet's agent core is design-only (`design/wq3806_limpet_architecture.md` §6; same note in
`critic.py`/`model_router.py`), so `run_suite(suite, agent)` takes the agent as a callable and
never places a model call. With no agent core, the honest baseline is zero, which the CLI's
default `null` agent reports (`limpet-bench run --agent null` → `score: 0.0`) — the forcing
function the ticket wants. `oracle` returns ground truth and scores 1.0 as a harness self-test.
`periodic()` wraps the single-shot run in an injectable-sleep loop (the `daemon.py` shape) and
emits `schema: limpet-bench/1` JSON-lines records that make a re-run resumable on unchanged
task digests (the macbench `row_digest` idiom).

**Scope held back, on purpose.** The real AgentRE-Bench task set is **not** vendored here:
fetching it needs network egress the build sandbox lacks (`fleet-search web` down fleet-wide,
wq#2595; no web tool granted — the exact wall the prior wq#3997 attempt hit). This ticket ships
the instrument plus a committed **offline fixture** (`limpet/benchmark.d/agentre_fixture.toml`)
modeling the published task schema, firmware-leaning to match the appliance. Vendoring the live
dataset and confirming its firmware/hardware task proportion against D1's criterion is a
follow-up needing egress — a drop-in file, not a code change. Deploy wiring of the periodic run
(a systemd timer / detached `lumen-run`, never foreground-unattended per instinct 5) rides with
that follow-up.

## wq#4010 — ESP32-S3 / ESP32 Bit Pirate bench-tool catalog entries

**D1 — offensive RF/Wi-Fi/BLE/sub-GHz/ESP-SDR modes stay out of the Limpet default image.**
ESP32 Bit Pirate (geo-tp/ESP32-Bit-Pirate) ships Wi-Fi deauth, BLE spoof/sniff, sub-GHz/RFID
replay, and (as of v1.8) an experimental "ESP SDR" adapter in the same firmware binary as the
bench-tool modes this ticket cares about (flashrom serprog, SUMP logic analyzer, OpenOCD
JTAG/SWD bridge). The project documents these as educational/interoperability features, but
Limpet's bench-bus scope is "talk to a chip on a bench," not "attack a network," and shipping
offensive RF/Wi-Fi tooling in a default appliance image creates an attractive-nuisance and
scope-creep problem the catalog should not carry silently. Decision: when a Bit Pirate tool
manifest is registered under `limpet/registry/tools.d/` (not yet done — the firmware itself
lives outside this repo, see Scope below), its `capabilities` list must enumerate only the
bench-bus modes (serprog, sump, openocd-jtag, uart-bridge) and must NOT include
wifi/deauth/ble-spoof/subghz-replay/rfid-clone/sdr capabilities, regardless of what the
underlying firmware build supports. This mirrors the registry schema's own posture
(`registry/schema.py`: "unknown == makes no claim") — omission from `capabilities` is the
enforcement mechanism, not a separate blocklist.

**D2 — catalog entries note CDC ambiguity instead of guessing a VID:PID.**
`limpet/hardware.d/03-multiprotocol-tools.toml` (ESP32 Bit Pirate entry) and
`limpet/target-devices.d/generic-usb.toml` (Espressif `303a:*` wildcard entry) both record
`usb_ids = []` / a VID-only wildcard rather than a specific PID: any ESP32-S3/C3/C6 running
TinyUSB CDC — Bit Pirate or otherwise — enumerates under Espressif's VID with a
firmware-chosen PID, and Bit Pirate v1.7+ deliberately varies its PID to dodge host-side USB
caching. This is the same ambiguity class already on record for the FTDI-VID Bus Pirate v3 /
JTAGulator entries: VID:PID alone cannot identify the device; a USB string-descriptor or
protocol probe is required. `confidence` is `"unverified"`/`"low"` on both entries because
Frank's physical S3 board has not been attached to any host this fleet can reach this session
(see Scope), and fleet-search web is down fleet-wide (wq#2595) so even the public Espressif
VID convention could not be independently re-verified — see each entry's `proprietary_notes`.

**Scope held back from the wq#4010 ticket text, on purpose.** The ticket's items (1) vendor a
pinned geo-tp/ESP32-Bit-Pirate release + fix the README, and (5) validate esp32-image-parser
on an S3 image, both target `re/common/tools/firmware-to-flash/README.md` and
`re/common/tools/TOOL_REGISTRY.md` — paths under `/home/lumen/re`, which exists but is
outside every `--add-dir` this worktree was granted (confirmed: `test -d` succeeds, `ls`/`grep`
on the same path is refused as outside allowed directories). Items (2) identify Frank's S3
module and (3) validate serprog/SUMP against the Pico baseline on a spare SPI NOR chip both
need the board physically attached to a reachable host, which it is not (re-confirmed this
session; matches the wq#4010 research addendum A4). Per the fleet's physical-task posture,
both gaps are queued rather than guessed around: wq#4037 (repo(/home/lumen/re) access, items
1/5) and wq#4036 (physical bench task, items 2/3). This entry and the two catalog edits above
are the items (4, 6) answerable from inside this worktree.

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
