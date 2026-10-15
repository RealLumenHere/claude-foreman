# TOOL_REGISTRY — fleet-level tool adoption decisions

Tracks RE/dev tools evaluated for fleet-wide adoption (MCP registration through
Commander, cross-host install) that are significant enough to need a documented go/no-go,
distinct from the routine `/home/lumen/bin` inventory in minerva's FLEET_TOOLS.md.

## Maksoor RE workstation — verified tool inventory (2026-10-14, wq#3803)

Host: `maksoor` (user `maksoor`, tailnet 100.81.204.74; the `maksoor` ssh alias on Lumen now
resolves to this — it did not before wq#3803). Probed live over ssh on 2026-10-14. "PATH" below means the
non-interactive ssh PATH (what Commander/fworker/scripts see).

| Tool | Version | Location | On non-interactive PATH |
|---|---|---|---|
| Ghidra (+ `analyzeHeadless`) | 12.1.3 | /opt/ghidra (`ghidraRun` in /usr/local/bin) | analyzeHeadless: no (use /opt/ghidra/support/analyzeHeadless) |
| REA (`rea-agents`) | 6.4.0 | ~/.npm-global/bin/rea | NO — interactive .bashrc only; MCP entry uses the absolute path |
| binwalk | installed | /usr/bin/binwalk | yes |
| unblob | 26.6.4 | /usr/local/bin/unblob | yes |
| frida | 17.18.0 | /usr/local/bin/frida (+ /opt/re-venv) | yes |
| openocd | 0.12.0 | /usr/bin/openocd | yes |
| flashrom | 1.6.0 | /usr/sbin/flashrom | yes |
| radare2 | installed | /usr/bin/radare2 | yes |
| rkdeveloptool | installed | /usr/local/sbin/rkdeveloptool | yes |
| mtkclient (`mtk`) / edl | installed | /opt/re-venv/bin/{mtk,edl}; /opt/mtkclient | NO — venv path only |
| heimdall | installed | /usr/bin/heimdall | yes |
| ollama | NOT INSTALLED | — | — |

Notes: `rea` and `mtk`/`edl` are intentionally not on the system PATH (home/venv installs). Maksoor
also carries the longer tool list recorded in lumen_state (`maksoor.node_provisioning`: cutter, jadx,
apktool, scrcpy, sigrok, etc.); only the tools above were re-verified on 2026-10-14.
There is no local LLM runtime on Maksoor (`ollama` absent) — inference for RE work goes through Lumen/LiteLLM.

## REA (Reverse Engineer Anything) — github.com/morluto/rea / npm `rea-agents`

**Status (2026-10-15, wq#4006): version drifted 6.4.0 -> 6.5.0 on Lumen (npm global install dir
mtime 2026-10-14 22:26) with still no PROVENANCE/SHA256, unlike `/opt/ghidra-mcp/6.0.0/PROVENANCE`.
`ops/wq4006-rea-security-rescope-a1/` ships the fix for hold preconditions 1 and 3 below (pin +
provenance, and move the MCP registration from Lumen-wide user scope in `~/.claude.json` to a
project scope at `~/re`, mirroring `ghidra-lumen`'s scope at `/data/lumen/archive/re`) plus a
self-update guard and a hostile-input policy for RE tooling (`common/tools/HOSTILE_INPUT_POLICY.md`,
none existed before). Per `ops/VERIFY_CONTRACT.md` the bundle is shipped, not applied — `~/.claude.json`
and `~/re` are outside a sandboxed BUILD worker's granted dirs, and `~/.claude.json` gates every
Claude Code session on Lumen, so the live cutover is a privileged/non-worker step (see that bundle's
RUNBOOK.md). Until applied, the state below (superseded install, status as of 2026-10-14) remains live.**

**Status (2026-10-14): INSTALLED on Lumen and Maksoor, Ghidra provider only. The original
SECURITY HOLD (below) is superseded by the installs, but its preconditions are NOT shown as met — see
"Open hold preconditions".**

Live state, verified 2026-10-14 (wq#3803):

- Package: `rea-agents@6.4.0`, npm global install on both hosts (`~/.npm-global`). `rea --version` -> 6.4.0.
- Lumen: `/home/lumen/.npm-global/bin/rea`. Non-interactive shells (ssh/Commander/fworker) now get
  `~/.npm-global/bin` on PATH plus `GHIDRA_INSTALL_DIR=/opt/ghidra_12.1.3_PUBLIC` and
  `JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64` from the `rea-env wq3803` block in `~/.bashrc`
  (above the interactive guard, next to the wq3511 block; backup `.bashrc.bak.*_pre_wq3803`).
- Maksoor: `/home/maksoor/.npm-global/bin/rea`, Ghidra at `/opt/ghidra` (12.1.3). Not on the
  non-interactive PATH; the MCP entry calls it by absolute path.
- Provider: Ghidra 12.1.3 only (compatible 12.1.x). Hopper and IDA are NOT configured (optional, not needed).
- Skill: `reverse-engineer-anything` v37 / 170 tools, aligned on both hosts. Installed with
  `rea setup --yes --skill-client shared` (writes only `~/.agents/skills/reverse-engineer-anything`;
  no client config touched). `rea doctor --provider ghidra` on Lumen: healthy, environment_healthy.
- MCP registrations (in Lumen `~/.claude.json`, so every Claude Code session on Lumen sees them):
  `rea` (local, `rea mcp`) and `rea-maksoor` (ssh-stdio to maksoor@100.81.204.74, absolute path to rea).
  No other client (Cursor, Codex, Gemini CLI, ...) is detected or configured.
- ssh alias: `ssh maksoor` now resolves on Lumen (Host block in `~/.ssh/config`, backup `config.bak.*_pre_wq3803`).
- Not done (optional, wq#3803 item 4): pwntools venv / `REA_PWNTOOLS_PYTHON` for `inspect-binary-layout`.

### Open hold preconditions (from the original hold, still needing a human call)

The installs landed (wq#3786 follow-ups) without the registry being updated, so none of these is
recorded as satisfied anywhere in this repo. Frank should either accept the current state or require them:

1. Pin to a specific commit/release hash. Installed version is 6.4.0 (now drifted to 6.5.0), but no
   commit hash / integrity record of what was installed or how (`npm install` vs
   `npx rea-agents setup`) exists here. **wq#4006**: `ops/wq4006-rea-security-rescope-a1/generate_provenance.py`
   writes `PROVENANCE.json` (version + npm `dist.integrity`) once the bundle is applied; not yet applied.
2. Read the setup script's install/postinstall hooks in an isolated throwaway environment first.
   No record that this was done. Still open.
3. Scope the MCP registration to a single non-production lane and scope Maksoor ADB access. Current
   registration is Lumen-wide for Claude Code (and `rea-maksoor` reaches the RE host that has live
   ADB/device access) — i.e. NOT scoped to one lane. **wq#4006**: `ops/wq4006-rea-security-rescope-a1/`
   moves `rea`/`rea-maksoor` to a project scope at `~/re`; not yet applied (privileged step, see
   that bundle's RUNBOOK.md). Maksoor-side ADB access itself is unchanged by this bundle — the Lumen
   registration no longer reaches it fleet-wide, but a symmetric guard on the Maksoor host is a
   separate follow-up.
4. Re-check for security advisories once the package has had more scrutiny. Not re-run on 2026-10-14.
   wq#3998/wq#4000 (LIMPET dissection tickets, in flight 2026-10-15) add scrutiny of REA's own repo
   but are not a security-advisory re-check.

### Original decision (2026-10-13, superseded — kept for the record)

Ticket wq#3786 asked to install REA fleet-wide: `npx rea-agents setup` on Lumen, install on
Maksoor for device RE work, configure a Ghidra provider, and register it as an MCP server so
every agent gets it through Commander. The first pass recorded a SECURITY HOLD instead of installing:

- The repo is real and MIT-licensed (covered by multiple independent outlets — dev.to, Medium,
  gbhackers, kitploit — as an MCP server/CLI published on npm as `rea-agents`).
- The repo was ~2 weeks old and its GitHub star count went 120K -> 140K -> 171K over roughly a
  week (press timestamps from `fleet-search`, 2026-10-13) — the signature of bot-driven virality, the
  pattern used to pressure agents into auto-installing a new MCP server on social proof alone.
- No independent security audit or advisory was found either way for a 2-week-old package.
- Blast radius if wrong/intercepted: Ghidra execution + live ADB/APK access to a real device
  (Maksoor) + an MCP registration that hands every Commander-routed agent that access, on two hosts,
  for a package pinned to `latest` rather than a reviewed commit/version.
- Fleet precedent treats trending-tool adoption this way: `kb/tooling-findings.md` (2026-09-01)
  gates adoption of Langfuse/Gatus/etc. rather than installing on sight.

See `kb/ISSUE-LOG.md` row 1277 for the logged finding.

## Pretender — automated peripheral modeling for firmware re-hosting (wq#3877, NO-GO)

**Status (2026-10-14): NOT adopted. Pretender's catalog entry is tracked as a JSON row in
`tool-catalog.json` per wq#3850 — there is no `limpet/examples/pretender.toml` and no
promotion-tier/gating mechanism currently exists in the repo (`limpet/examples/` has only
generic per-tool templates for ghidra, ghidra-mcp, and hackrf). This NO-GO verdict stands on
its own merits below, independent of any tiering scheme.**

What it is: PRETENDER, from Gustafson et al., "Toward the Analysis of Embedded Firmware
through Automated Re-Hosting" (RAID 2019, UCSB seclab — abstract at
http://subwire.net/publication/pretender/, repo at https://github.com/ucsb-seclab/pretender).
Records MMIO traffic between real hardware and its firmware, infers per-register peripheral
models from the trace, then replays the firmware against those models in a fully emulated
environment so it can run without the physical device.

- **Recording setup requirements**: hardware-in-the-loop — a real JTAG/SWD-debuggable MCU
  board, OpenOCD, and the `avatar2` framework synchronizing the board with QEMU. The
  project's own README (fetched 2026-10-14 via `fleet-search read
  https://github.com/ucsb-seclab/pretender`) calls this "rather fragile to set up and
  deploy," warns that "small version changes in any of these components can cause various
  things not to work as expected, or behave intermittently, particularly regarding
  interrupts," and notes even "the host system's hardware, such as the USB bus
  controllers... have caused nondeterministic issues during our experiments." It is pinned
  to avatar2 1.3.0 and Ubuntu 16.04, "as of 9/17/2019" — a frozen, EOL-era toolchain with no
  evidence of maintenance since. This is a physical-lab dependency (board + probe + fragile
  driver stack), not something a headless fleet worker can stand up unattended.
- **Model accuracy**: the paper's own claim is that the inferred models are "interactive,
  stateful, and transferable." The successor work, Conware (PurS3 Lab, "Automated Modeling
  of Hardware Peripherals"), contradicts that in its abstract/findings as surfaced via
  `fleet-search web "Conware Pretender accuracy limitations stateful peripheral model
  comparison"`: "Pretender is incapable of replaying peripherals that were not [exercised
  along the same path as recording]" — i.e. the models overfit to the linear trace captured
  during recording and do not generalize to firmware inputs or code paths that diverge from
  what was recorded. That is a disqualifying gap for the primary use case re-hosting exists
  for (fuzzing / symbolic execution needs to explore *new* paths, not replay the one that was
  recorded). Conware (2023) and NLP-manual-driven approaches like SEmu were built
  specifically to close this gap, i.e. the field has already moved past Pretender's modeling
  approach.
- **Qiling/Unicorn integration**: Pretender's replay target is QEMU via `avatar2`, a
  full-system emulator with its own MMIO device-model hooks — not Unicorn (bare CPU
  emulation) or Qiling (Unicorn-based OS/firmware emulation framework, already in LIMPET's
  stack per `firmware-emulation`/Qiling usage elsewhere in this repo). No adapter exists
  from Pretender's inferred register models to Qiling's hook-based peripheral API
  (`ql.hook_mem_*`); bridging them would mean re-implementing each inferred model as a
  Unicorn MMIO hook by hand, which is a rewrite, not an integration.

**Verdict**: no-go for fleet adoption. Physical-hardware recording rig the fleet doesn't
have, a frozen 2019-era dependency chain with no path to Qiling/Unicorn, and a core modeling
approach its own successor paper shows doesn't generalize past the exact trace it was
recorded from. Pretender's catalog entry remains the JSON row in `tool-catalog.json` per
wq#3850, with no promotion or further action taken. If automated peripheral modeling for
Qiling/Unicorn re-hosting is still wanted,
Conware or an NLP/hardware-manual-driven approach (SEmu) is the better next eval — not a
Pretender integration.

## Fuzzware — automated MMIO modeling for firmware fuzzing (wq#3875, NO-GO for the proposed pipeline)

**Status (2026-10-14): NOT adopted. The ticket's proposed pipeline (unblob extract →
Fuzzware MMIO model → Qiling emulation with auto-generated handlers) doesn't hold together
because the two tools operate on different artifact classes — see "Scope mismatch" below.
`re-tools-analysis.md`, which the ticket cites as a ref, was not found anywhere in this
worktree or the other `--add-dir` roots (kb, foreman, bin, sentinel, mailbot, pokemon,
agents/work-queue) as of this evaluation; this entry is sourced directly from the project's
own repos instead.**

What it is: Fuzzware (Scharnowski, Bars, Schloegel, Gustafson, Muench, Vigna, et al.,
"Fuzzware: Using Precise MMIO Modeling for Effective Firmware Fuzzing," USENIX Security
2022, RUB Chair for Systems Security — https://www.usenix.org/conference/usenixsecurity22/presentation/scharnowski,
repo https://github.com/fuzzware-fuzzer/fuzzware). Unlike Pretender, it needs no physical
device: it statically configures the memory ranges of an **ARM Cortex-M3/M4** firmware
image, then emulates and fuzzes it directly, using symbolic execution (angr) to infer which
MMIO registers actually influence control flow and modeling the rest automatically instead
of hardware-in-the-loop recording.

- **Install complexity**: Docker is the documented path (`./build_docker.sh` /
  `./run_docker.sh <dir> fuzzware pipeline ...`, per the repo README, fetched 2026-10-14 via
  `fleet-search read https://github.com/fuzzware-fuzzer/fuzzware`). Native install
  (`./install_local.sh`) sets up two separate Python virtualenvs because "our MMIO modeling
  component [is based] on angr version 8.19.10.30... this version of angr only supports
  Python versions lower than 3.10" — the install script auto-detects this and needs
  `MODELING_VENV_PYTHON3` pointed at a pre-3.10 interpreter. Workable (the project scripted
  around its own version pin, unlike Pretender's abandoned Ubuntu-16.04/avatar2-1.3.0
  chain), but it's a 2022-era angr pin with no stated upgrade — the README's own "future
  work" section lists "Upgrade angr version... to support Python in version >=3.10" as
  still open.
- **Python API**: there isn't a clean importable one. Fuzzware ships as a CLI
  (`fuzzware -h`, `fuzzware <util> -h`) orchestrating three components — `fuzzware-emulator`
  (the Unicorn-based run engine), `modeling` (angr-based MMIO inference), and
  `fuzzware-pipeline` (glues modeling + emulation + AFL/AFL++ together). Driving it
  programmatically means shelling out to the CLI or importing internal pipeline modules
  directly, not a stable public API surface.
- **Output format**: the inferred model is a YAML `config.yml` under `mmio_models:`, keyed
  per access **site** — `pc_<PC>_mmio_<addr>` — not per register. Each entry names a model
  type (`constant`: serve a fixed value; `bitextract`: narrow/shift a fuzzer-supplied byte
  into a bitfield; `passthrough`: treat it as ordinary backed memory; confirmed via
  `fleet-search read` on `fuzzware-emulator/README_config.yml`) plus the `addr`/`pc` pair and
  model-specific fields. This is a precise, machine-readable format, but the PC-qualification
  matters for the next point.
- **Qiling/Unicorn integration**: Fuzzware's own README states it "works by plugging an
  instruction set emulator (currently: Unicorn Engine) to a fuzzer (currently: afl/AFL++)" —
  it targets bare Unicorn directly, not Qiling, and ships no Qiling adapter. Porting a
  `config.yml` model to Qiling would mean writing a translation layer: Qiling's
  `ql.hook_mem_read`/`hook_mem_write` key on address range only, but Fuzzware's models are
  keyed on (address, PC) pairs, so each hook would additionally need to check the current PC
  against the model's site before applying `constant`/`bitextract`/`passthrough` behavior.
  More tractable than Pretender's case (a structured YAML table beats re-deriving avatar2
  device models by hand) but still a bridging layer that doesn't exist today, not a drop-in.
- **Resource requirements**: no JTAG/SWD rig or physical board — this is a genuine advantage
  over Pretender and over hardware-in-the-loop MMIO recording generally. Cost shifts instead
  to compute: angr symbolic execution during modeling and a sustained AFL/AFL++ fuzzing
  campaign (coverage fuzzing benefits from long wall-clock runs and multiple parallel
  instances), both CPU-bound, no GPU needed.
- **Scope mismatch with the proposed pipeline**: Fuzzware's own opening description targets
  "an ARM Cortex-M3/M4 firmware image" run "without full device emulation" — i.e. a flat,
  bare-metal MCU firmware blob with no OS and no filesystem. The README's "future work"
  section confirms this isn't incidental: "Currently, the Fuzzware code is rather tightly
  coupled with ARM/Cortex-M." `unblob` (this repo's `firmware-extraction` skill), by
  contrast, extracts filesystems and nested images out of Linux-class embedded firmware
  (routers, IoT gateways — typically MIPS/ARM/etc. SoCs running an OS with an MMU). A
  bare Cortex-M MCU blob has no embedded filesystem for unblob to carve out in the first
  place, so the ticket's first pipeline stage doesn't feed the second: `unblob extract` and
  `Fuzzware MMIO model` are solving for two different artifact classes, not two stages of
  one pipeline.

**Verdict**: no-go for the pipeline as scoped. Fuzzware is a reasonable, better-engineered
answer than Pretender to one specific question — bare-metal Cortex-M MCU firmware fuzzing
without a hardware rig — but that question isn't the one `unblob`-extracted, Linux-class SoC
firmware poses, and nothing in LIMPET's current firmware intake produces the bare Cortex-M
blobs Fuzzware is built for. If a bare-metal MCU target shows up (no OS, single flat image,
ARM Cortex-M3/M4), Fuzzware is worth a follow-up eval on its own merits — add it as a
community-tier `limpet/examples/fuzzware.toml` catalog entry at that point, not now, since
there is no current intake path that would exercise it.

## Sootty — terminal VCD waveform viewer (wq#3878, NO-GO for xfce4-terminal / Lumen's headless terminal as-is)

**Status (2026-10-14): NOT adopted for the UX the ticket wants.** `re-tools-analysis.md`,
which the ticket cites as a ref, was not found anywhere in this worktree or the other
`--add-dir` roots (kb, foreman, bin, sentinel, mailbot, pokemon, agents/work-queue) — same
gap as the Fuzzware/Pretender entries above. This entry is sourced from the project's
upstream repo and docs instead (`fleet-search read` on
https://github.com/Ben1152000/sootty, fetched 2026-10-14, plus `fleet-search web` on the
rendering-protocol and sigrok-format questions below).

What it is: Ben1152000/sootty, a Python CLI (`pip install sootty`) that parses a VCD/EVCD
waveform and renders it as an SVG, with a Python API (`WireTrace.from_vcd_file`,
`Visualizer(Style).to_svg`) alongside the CLI, plus query-language filtering/breakpoints
(`-s`/`-e`/`-b`/`-w`) and a save/reload mechanism for queries.

- **VCD format support**: good. Standard VCD loads directly via `WireTrace.from_vcd_file`;
  Extended VCD (EVCD) is supported through a bundled `sootty.utils.evcd2vcd` converter that
  runs ahead of the same VCD loader. This is the one criterion Sootty clears cleanly.
- **Sigrok integration**: none built in — Sootty only ever reads a VCD/EVCD file from disk,
  it has no sigrok-aware input path. The pairing works only as a two-tool pipe: sigrok's own
  "Input output formats" wiki page and ELCE-2013 slides list VCD as a native `sigrok-cli -O
  vcd` output format (independently confirmed by a real-world EEVblog report of using exactly
  that flag), so `sigrok-cli -O vcd ... capture.vcd` into `sootty capture.vcd` works, but it's
  a shell pipeline the ticket would have to script, not an integration Sootty ships.
- **Rendering quality in xfce4-terminal — the disqualifying finding**: Sootty is not a
  text/ASCII-native renderer despite the "terminal-native" framing in the ticket. Per its own
  README, the CLI's terminal path is SVG generation (`sootty "waveform.vcd" -o > image.svg`)
  piped through two external binaries it depends on but does not vendor: `rsvg-convert`
  (SVG → raster) and `viu` (raster → terminal image). `viu`'s own docs state it displays
  images via the Kitty graphics protocol, iTerm2 inline images, or Sixel, "down to Unicode
  block characters as a universal fallback." xfce4-terminal (VTE-based) implements none of
  the first three: it has no Kitty or iTerm2 protocol support, and the Sixel toggle it added
  in version 1.1.0 is a no-op unless the underlying VTE library was itself built with Sixel —
  which upstream GNOME VTE does not ship in stable releases (open issue
  gitlab.gnome.org/GNOME/vte/-/issues/253; corroborated by arewesixelyet.com listing
  "xfce-terminal: Needs Patch" for exactly this reason). So in Lumen's actual xfce4-terminal,
  every Sootty render would silently fall all the way to viu's lowest-fidelity Unicode
  half-block approximation of the rasterized SVG — legible at a glance, but a blocky bitmap
  substitute, not the crisp vector/protocol-native display the project demos on Kitty,
  WezTerm, or Contour.
- **Install footprint**: none of `sootty`, `viu`, or `rsvg-convert` are present on Lumen
  (`which sootty viu rsvg-convert sigrok-cli` found only `sigrok-cli`, at `/usr/bin/sigrok-cli`).
  Getting even the degraded fallback working means standing up a Python package, a Rust
  binary (`cargo install viu`, no apt package assumed present), and a C library binding
  (`librsvg2-bin`) for a payoff capped at block-art fidelity on the fleet's real terminal.

**Verdict**: no-go as a Limpet UX win on xfce4-terminal. The ticket's "terminal-native,
renders in TTY without GUI" framing doesn't hold — Sootty needs an SVG rasterizer and an
image-capable terminal to show anything at all, and Lumen's xfce4-terminal isn't one
(stock VTE, no Kitty/iTerm2/Sixel). sigrok pairing is real but is a plain `-O vcd` pipe, not
an integration. If a genuinely text-native VCD viewer is wanted for headless Limpet use, the
next eval should target a tool that renders ANSI/Unicode waveforms directly (no SVG
intermediary, no external rasterizer) rather than a second graphics-protocol-dependent tool.

## exploitdb / searchsploit — offline Exploit-DB mirror (wq#3942, ADOPTED)

**Status**: catalogued (`limpet/registry/tools.d/searchsploit.yaml`) and wired into a new
MCP tool (`ops/wq3942-limpet-exploit-db-a1/server.py`, `limpet_exploit_lookup`). Not yet
`apt-get install`-ed anywhere — there is no ISO build pipeline in this repo to install it
into (confirmed: no `packages.txt`/`Dockerfile`/live-build config anywhere), the same
"catalog ahead of the pipeline" posture `limpet/hardware.d/build_iso_drivers.py` takes for
driver bundles.

Carried over from `research/WQ3921_EXPLOIT_VULN_DATABASES.md` §2/§9: `searchsploit` is the
`exploitdb` apt package (same one Kali ships), a git clone of
`offensive-security/exploitdb` plus a CSV index, no server/DB engine, low hundreds of MB,
self-updates with `searchsploit -u`. Weak on embedded/SoC boot-ROM exploits (that's what
the paired curated `device-exploits.db` below is for) but a useful fallback for "any known
exploit mentioning this vendor/model string."

Paired with it, a new fleet-authored layer the research flagged as "most valuable and
least available": `/var/lib/limpet/exploit-db/device-exploits.db` (SQLite,
`devices(soc_family, vendor, model, exploit_type, exploit_tool, cve_id, description,
confidence, source_url)`), seeded from `research/wq3921_soc_exploit_seed.json` via
`ops/wq3942-limpet-exploit-db-a1/device_exploit_db.py`. Both are queried together by
`limpet_exploit_lookup(soc_family, vendor, model)`, the call target for the Target Device
Wizard's SUGGEST stage (`docs/target-wizard-spec.md` §4.4, step 3 of the wizard).

**Verdict**: adopt both layers as designed by wq#3921; implementation lands in
`ops/wq3942-limpet-exploit-db-a1` (this ticket). Actually installing the `exploitdb` apt
package onto a live LIMPET host is still blocked on the ISO build pipeline not existing yet
— see that bundle's README "Packaging searchsploit" section.

## License audit — OGhidra / EMBA / FACT / FAT (Attify) 2.0 (wq#3959)

**Status**: audit only, no adoption decision — EMBA/FACT/FAT 2.0/OGhidra are wq#3929 P0
candidates, none are catalog rows yet. Full per-tool license text, redistribution-posture
analysis (external-invoke vs. bundling into the sold Limpet ISO), and the research-access
gaps hit while verifying are in `design/wq3959_license_audit_oghidra_emba_fact_fat.md`.
Headline: EMBA and FACT are both GPL-3.0 (safe to bundle with a source offer, no
commercial-use restriction); FAT 2.0 is FSL-1.1-ALv2 (a "Competing Use" clause that plausibly
blocks bundling it into a commercially-sold appliance without a written license from Attify —
external-invoke only until that's resolved); OGhidra's license could not be independently
verified this session (search tooling down, see the audit doc) and is treated as
external-invoke-only pending confirmation, same conservative posture as FAT 2.0. The license-risk
tracking convention this audit added to `limpet/tool-catalog.json` (`provenance.license_risk_policy`,
plus `license_risk`/`redistribution_posture` fields) applies to whichever of these four actually
gets a catalog row when wq#3929's P0 list lands.
