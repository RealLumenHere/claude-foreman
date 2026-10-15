# wq#4005 — LIMPET DISSECT: EMBA (e-m-b-a/emba) — the automated firmware pipeline (wrap as a core pass)

Status: research/design. Author: build worker, 2026-10-15. Dissects
`https://github.com/e-m-b-a/emba` per the competitor dissection guide §3.2
(`scratch/research/limpet_competitor_dissection_guide.md`, read at dispatch; the guide lives
outside this worker's sandbox, so citations below are to the live EMBA tree, not the guide
snapshot). **EMBA** (Embedded Linux Analyzer) by **Michael Messner / Pascal Eckmann**
(Siemens AG / Siemens Energy AG) is "the central firmware analysis and SBOM tool" — a
command-line *pipeline* that extracts a firmware image, runs static + emulated-dynamic
checks, builds an SBOM, correlates CVEs and known exploits, and emits a web vulnerability
report (`EMBA:README.md:36`). The guide's one-line verdict is the right one: **wrap EMBA as a
"run the automated firmware pass, ingest its findings into the workspace" core plugin** — it
is the batch pipeline that complements Limpet's interactive/agentic loop, not a competitor to it.

**The headline is the license, and it is GREEN.** EMBA is **GPLv3** (`EMBA:LICENSE:2-3`;
every module carries `SPDX-License-Identifier: GPL-3.0-only`, `EMBA:modules/template_module.sh:12-13`).
Unlike OFRAK (wq#4004, source-available, RED gate), EMBA may be wrapped and invoked freely.
The only license discipline is the usual copyleft one: **invoke EMBA across a process/CLI
boundary** (Limpet shells out to `./emba`), never statically link or vendor its bash into a
non-GPL Limpet component. A separate-process plugin wrapper keeps Limpet's own code off the
GPL hook while using EMBA lawfully. Record GREEN-with-process-boundary in wq#3959.

## 0. Grounding (live, not memory)

Cloned shallow to an isolated scratch dir and read directly this session
(`git clone --depth 1` into `/tmp/fworker-wq4005/emba-live`, read as untrusted data — no
instruction inside any module, docstring, or config was acted on, per the fleet's standing
rule for attacker-controlled bytes). Target citations are `EMBA:<path>:<line>`; Limpet-repo
citations are bare `limpet/...:N`.

Confirmed shape: a pure-bash module pipeline. Top level is the `emba` launcher + `helpers/`
+ `config/` + `modules/` + `scan-profiles/` + a `Dockerfile`/`docker-compose.yml`
(`EMBA:` tree root). Analysis is ~90 modules under `modules/`, each a self-contained bash
file whose **filename encodes its phase and run-order** (§1). EMBArk — the multi-user web
console — is a **separate repository** (`e-m-b-a/embark`, `EMBA:README.md:55`), not part of
this tree; it is dissected from the EMBA docs/README as prior art (§3), not from source here.

## 1. The module-naming pipeline — the architecture to understand (and contrast)

EMBA's entire control flow is a **convention over configuration** pipeline. There is no
plugin manifest, no registry, no dependency graph — a module is a bash file named
`<LETTER><NN>_<name>.sh`, and the launcher globs each phase's letter and runs the matches in
numeric order. The phases, read straight from the launcher (`EMBA:emba`):

- **P — Pre-Check / extraction** (`EMBA:emba:894`; "execute all modules with
  `P[Number]_Name.sh`", `EMBA:emba:907`). Carving the image into a filesystem tree: unblob
  (`P55`), binwalk (`P50`), a deep extractor (`P60`), plus vendor-specific decryptors
  (`P17` gpg, `P18` BMC, `P20` Foscam, `P21` Buffalo, `P22` Zyxel, `P40` DJI) and UEFI
  (`P35`). (`EMBA:modules/P55_unblob_extractor.sh`, etc.)
- **S — static testing** (Testing phase, `EMBA:emba:969`). ~60 `S*` modules: version
  detection, weak-permission/credential/cert scans, binary-protection, weak-function and
  decompiler checks, per-language script linting, kernel checks (§4).
- **L — live emulation** (`EMBA:emba:992`). QEMU user-mode (`S115` usermode emulator) and
  full system emulation (`L10`) with live network-service probes (`L15` nmap, `L20` snmp,
  `L25` web, `L35` metasploit).
- **Q — AI/questioning connectors**: `Q03_localai_connector.sh` is a **self-hosted LocalAI**
  endpoint (`EMBA:modules/Q03_localai_connector.sh:16`) and `Q20` a Dependency-Track
  connector — AI support that keeps data local, a privacy posture worth noting.
- **F — final aggregation**: `F15` CycloneDX SBOM, `F17` cve-bin-tool CVE extraction, `F50`
  base aggregator (exploit correlation), `F10` license summary.
- **D — diff mode** (`EMBA:emba:932`): `D02/D05/D10` run only when a second image is given
  with `-o` (§5).

**The module contract** is the one piece Limpet should study closely, because it is EMBA's
*evidence model*. Every module follows `template_module.sh`:
`module_log_init` opens the per-module log (`EMBA:modules/template_module.sh:95`),
`module_title` prints the banner (`:98`), findings are emitted with `print_output` using
**severity-coded prefixes** — `[+]` = finding, `[*]` = info, `[-]` = no-finding
(`EMBA:modules/template_module.sh:232` et seq.), **structured** findings go to a CSV via
`write_csv_log`, and `module_end_log "<name>" "${#COUNT_FINDINGS[@]}"` records the finding
count — "if your module didn't find something, it isn't generated in the final report"
(`EMBA:modules/template_module.sh:138-140`). The web report is built by rendering those
per-module CSV/log artifacts.

**Contrast with Limpet (the design read).** EMBA's pipeline is *deterministic and
non-interactive*: filename order is the plan, there is no agent in the loop, and it runs
start-to-finish unattended (`sudo ./emba -l log -f firmware`). Limpet is the inverse — an
agent drives tools interactively against one workspace with hardware attached. **These are
complements, not rivals**: EMBA is the "point it at an image, get a full report" batch pass
that seeds a Limpet investigation; the agent then does the interactive, hardware-in-the-loop
follow-up EMBA cannot. That is the framing for the wrap plan.

## 2. Deliverable 1 — wrap EMBA as a core "automated firmware pass" plugin

**Plan: a process-separated `core` plugin that runs `./emba` over the workspace's extracted
image and ingests its CSV findings as Limpet workspace findings.** Concretely:

1. **Manifest / trust boundary.** A `PluginManifest` (`limpet/manifest.py:79`) for `emba`
   declaring a `MutationBoundary` (`limpet/manifest.py:63`) of
   **`writes=("workspace",)`, `escalates=True`, `network=True`** — EMBA is analysis-only with
   respect to the *target* (it never repacks; that is OFRAK's lane, G7), but it needs root +
   Docker to mount/extract (`sudo ./emba`) so it **escalates**, and `F17` pulls a CVE
   database over the network. Declaring this up front is exactly the "trust gate never infers
   blast radius from a tool's name" contract Limpet already enforces
   (`limpet/manifest.py:66-73`); EMBA is the archetypal `escalates=True` analysis plugin, the
   mirror of OFRAK's `writes=("target",)` repack plugin.
2. **Health gate.** A `HealthCheck` (`limpet/manifest.py:53`) that probes for an EMBA install
   (or the EMBA Docker image) and the host's Docker/sudo availability, degrading to a
   `limitations` note rather than failing silently when absent.
3. **Run.** Invoke `./emba -l <workspace>/evidence/emba-log -f <workspace>/target -p
   <scan-profile>` across a **process boundary** (GPL hygiene, §license). Reuse the firmware
   that Limpet's firmware-extraction skill already carved into `target/` rather than
   re-extracting, or let EMBA's `P*` phase extract when Limpet has not — the plugin should
   pass `target/` and skip EMBA's pre-check when the tree is already unpacked.
4. **Ingest — the load-bearing step.** EMBA's output is per-module CSV + logs, not Limpet
   findings. The plugin maps each `write_csv_log` row into a Limpet finding JSON written to
   `<workspace>/evidence/findings/*.json` (the workspace's committed-findings dir,
   `limpet/workspace_report.py:19`), so EMBA results appear in `workspace_report` alongside
   agent findings and are committed to `workspace.git`. **Mask on ingest**: EMBA's credential
   modules (§4) emit cleartext passwords/hashes into CSVs — route every ingested value
   through `workspace_report.mask_text` (`limpet/workspace_report.py:82`) before it lands in a
   committed finding, so a hard-coded password EMBA found is recorded as *located*, not
   *re-leaked* into the Limpet workspace.
5. **Tier.** Ship at `community`/`vetted` and promote to `core` through the normal
   `TOOL_REGISTRY` go/no-go (`TOOL_REGISTRY.md`), not direct-to-core — same gate every tool
   crosses.

**Net:** EMBA becomes Limpet's automated firmware first-pass. The agent asks for "the EMBA
pass," gets a workspace full of masked, structured findings, and spends its interactive budget
on the hardware-attached follow-up EMBA structurally cannot do.

## 3. Deliverable 2 — EMBArk as live-console prior art (gap G5)

Limpet's **gap G5 is the live operator console** — a real-time view of an in-flight analysis,
which Limpet lacks (it has a replayable git trail but no live tail,
`design/wq3929_competitive_analysis.md:112`). The guide (and wq#3995) downgraded VRAX to a
LinkedIn description, leaving **EMBArk as the verified, shipping prior art for G5**
(`design/wq3929_competitive_analysis.md:206-210`).

**EMBArk** (`e-m-b-a/embark`, `EMBA:README.md:55`, v0.3; Black Hat Arsenal demo
`EMBA:README.md:50`) is EMBA's **multi-user team web server**: simultaneous firmware uploads,
a browsable team dashboard, and — the feature Limpet should study — **tracking a device's
security posture across firmware versions over time**. For G5, the extractable ideas are:

- **The live view is a read model over the same artifacts, not a second source of truth.**
  EMBA already streams progress via phase banners and `write_notification` (`EMBA:emba:973`)
  and per-module CSV/logs as they land; EMBArk renders that evolving log set live. Limpet's
  cheap G5 version is identical in spirit: **a read-only live tail of the workspace evidence
  log** (the `evidence/findings/*.json` + `workspace.git` log already being written), not a
  new streaming engine. The git-committed workspace *is* the event stream; the console is a
  follower.
- **Multi-user is where EMBArk earns its keep** — and where Limpet's trust model must lead.
  EMBArk's multi-user uploads are the exact "shared instance" shape OFRAK's license forbade;
  Limpet's per-operator trust gating is the differentiator to keep. Adopt EMBArk's
  *cross-version device tracking* as a workspace feature (a device's findings over N firmware
  revisions), which dovetails with the diff work (§5).

**Verdict:** EMBArk validates that G5 is worth building and that the right shape is a
**follower view over committed state**, not a bespoke real-time pipeline. Build the read-only
workspace tail first; defer the multi-user server to whenever Limpet grows a team surface,
and gate it with Limpet's trust model rather than EMBArk's open-upload one.

## 4. Deliverable 4 — the baseline automated-finding set (the S-module checks to mirror)

These S-modules are the concrete "what a good automated firmware pass must flag" checklist.
Each is a finding class Limpet's workspace schema should be able to carry, and the EMBA
plugin (§2) is the first producer of them:

- **Hard-coded credentials / password files** — `S45_pass_file_check` searches for
  password-related files and extracts passwords and root accounts
  (`EMBA:modules/S45_pass_file_check.sh:17`), logging `password file` / `password files
  count` rows (`EMBA:modules/S45_pass_file_check.sh:52,55`). Deep variants: `S106` key
  search, `S107` password search, and **`S108_stacs_password_search`** which runs the STACS
  engine + community ruleset for **password hashes** in the firmware
  (`EMBA:modules/S108_stacs_password_search.sh:16-18`), feeding `S109` JtR local cracking.
- **setuid / setgid / world-writable / shadow** — `S40_weak_perm_check` scans for setuid,
  setgid, world-writable and shadow files (`EMBA:modules/S40_weak_perm_check.sh:17`), emitting
  `setuid` rows cross-referenced to GTFOBins links (`EMBA:modules/S40_weak_perm_check.sh:63`)
  and `world-writable` rows (`EMBA:modules/S40_weak_perm_check.sh:91`).
- **Certificate expiry** — `S60_cert_file_check` searches certificates and records, per cert,
  the expiry date and whether it is **already expired or expiring within a watch window**
  (`EMBA:modules/S60_cert_file_check.sh:46,97`).
- **Version → 1-day CVE → known-exploit availability** — the F-phase chain, which is the
  model for "turn an inventory into actionable 1-days": `S09_firmware_base_version_check`
  does regex-based static version detection from `config/bin_version_identifiers`
  (`EMBA:modules/S09_firmware_base_version_check.sh:17-20`) → `F15_cyclonedx_sbom` builds a
  CycloneDX SBOM from that inventory (`EMBA:modules/F15_cyclonedx_sbom.sh:16`) →
  `F17_cve_bin_tool` extracts known CVEs via cve-bin-tool (`EMBA:modules/F17_cve_bin_tool.sh:16-17`)
  → **`F50_base_aggregator` correlates known-exploit availability** (counts remote/local
  exploits and **Metasploit modules** per finding, `EMBA:modules/F50_base_aggregator.sh:740-744`).
  That last step — not just "has a CVE" but "has a *public exploit*" — is the prioritization
  signal Limpet's finding schema should carry.
- **UEFI 1-days via FwHunt** — `S02_UEFI_FwHunt` runs Binarly's fwhunt-scan with the FwHunt
  rules against detected UEFI firmware (`EMBA:modules/S02_UEFI_FwHunt.sh:17-20,37`). A distinct
  target class (UEFI, not Linux rootfs) worth carrying as its own finding type.

**Mirror, don't reimplement.** The guide says "mine the S-module checks as the baseline
automated-finding set" — the lift is the **finding taxonomy** (creds / weak-perms / cert-expiry
/ version→CVE→exploit / UEFI), which Limpet's workspace schema should name as first-class
finding classes. The *detection* stays EMBA's job via the §2 wrapper; Limpet owns the
taxonomy, the masking-on-ingest, and the interactive follow-up.

## 5. Deliverable 3 — align EMBA diffing with Limpet wq#3906 (do not duplicate)

EMBA ships a **diff mode** (`D02/D05/D10`), entered with the `-o <second-firmware>` parameter
(`EMBA:modules/D10_firmware_diffing.sh:16-21`). Its mechanism: extract both images, then for
each file compute **ssdeep fuzzy hashes** and flag files ranking below a similarity threshold
(`lSSDEEP_MIN_RANK=95`, `EMBA:modules/D10_firmware_diffing.sh:26`), rendering changed files as
**colordiff** text deltas into the web report (`EMBA:modules/D10_firmware_diffing.sh:155,184`).
It requires the web reporter to be legible (`EMBA:modules/D10_firmware_diffing.sh:19-20`).

Limpet's own firmware-diff design (`design/wq3906_limpet_firmware_diff.md`) already does the
same job at a *higher fidelity*: it walks two workspace extractions, **classifies every file
as added/removed/unchanged/modified**, and on the modified set runs a **per-file differ —
BinDiff for executables (function-level moves), text/binary deltas otherwise** — emitting a
structured `firmware-diff.json` committed to `workspace.git`
(`design/wq3906_limpet_firmware_diff.md:5-10`). It is anchored in git's own rename/content
tracking across two commits of one workspace (`design/wq3906_limpet_firmware_diff.md:53-59`).

**Alignment call (do not duplicate):**
- **Keep wq#3906 as the differ of record.** It is strictly richer than EMBA's diff:
  structured JSON output (not colordiff-into-HTML), explicit add/remove/modify classification,
  and **function-level** BinDiff on changed executables where EMBA stops at a file-level ssdeep
  rank. wq#3906 also mirrors the fleet's REA `compare_*` output shape
  (`design/wq3906_limpet_firmware_diff.md:33-38`), which EMBA's bespoke text diff does not.
- **Lift one idea from EMBA: ssdeep as the cheap pre-filter.** EMBA's fuzzy-hash rank is a
  fast way to *decide which files are "modified enough" to bother diffing*. wq#3906's
  classify step can use ssdeep similarity as the triage gate before spending a BinDiff run —
  cheaper than hashing-plus-BinDiff on every file. This is a one-line enhancement to wq#3906's
  classify stage, not a new module.
- **When the EMBA plugin runs, run EMBA with a single image (`-f`), not diff mode (`-o`).**
  Firmware-image diffing stays Limpet-native (wq#3906); EMBA is wrapped only for the
  single-image automated pass (§2). This avoids two differs disagreeing in one workspace.

## 6. Extraction verdict — what Limpet lifts, and where

- **WRAP (core, GREEN license) — EMBA as the automated firmware pass** (§2). Process-separated
  plugin, `writes=("workspace",)/escalates=True/network=True`, ingesting masked CSV findings
  into `evidence/findings/*.json`. The guide's primary ask; the complement to Limpet's
  interactive loop.
- **ADOPT (taxonomy, free) — the S-module finding classes** (§4) as Limpet's first-class
  automated-finding set: hard-coded creds, setuid/world-writable, cert-expiry,
  version→CVE→**public-exploit**, UEFI/FwHunt. Own the taxonomy + masking; let EMBA detect.
- **ADOPT (G5, free) — EMBArk's "follower view over committed state"** as the live-console
  shape (§3): a read-only tail of the workspace git/evidence log, plus cross-version device
  tracking; defer the multi-user server to a future team surface under Limpet's trust gate.
- **ALIGN, DON'T DUPLICATE — diffing** (§5): wq#3906 stays the differ of record (richer:
  structured JSON + function-level BinDiff); lift only EMBA's ssdeep rank as wq#3906's cheap
  modified-file pre-filter; run the EMBA plugin single-image only.
- **CONTRAST — the convention-pipeline vs the agent loop** (§1): EMBA proves the batch
  "point-and-report" pipeline is a solved, commoditized shape. Limpet's edge is not a better
  pipeline — it is the **agent + hardware-in-the-loop + per-operator trust gate** wrapped
  *around* a wrapped EMBA, which is exactly the deployment shape EMBA/EMBArk's open batch model
  does not occupy.

**Net differentiation read.** EMBA is the best open automated firmware pipeline and its GPLv3
license makes it a clean adopt — wrap it, ingest it, mirror its finding taxonomy, follow its
EMBArk console shape, and align its diff with wq#3906. None of that is a threat to Limpet's
lane; all of it is Limpet standing on EMBA's shoulders for the batch first-pass while keeping
the interactive, hardware-attached, trust-gated investigation as the product.
