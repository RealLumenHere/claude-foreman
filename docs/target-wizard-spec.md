# wq#3901 — LIMPET Target Device Wizard

Guided device onboarding with auto-identification. The wizard is the front door to a
Limpet investigation: an operator plugs in a device (or points Limpet at an image/dump)
and the wizard carries them from "something is attached" to "a workspace is open and the
first analysis step has run" without requiring them to already know what the device is.

This is a **design-only** document. It specifies behaviour, interfaces, and the data
model; it installs nothing and registers no MCP server. It builds directly on the Limpet
architecture (`design/wq3806_limpet_architecture.md`) and the plugin manifest schema
(`design/limpet/plugin-manifest.schema.json`), and consumes the vision component-ID
pipeline designed under **wq#3900** as its unknown-device fallback. Where this spec and
those documents disagree, they win — this is the orchestration layer on top of them, not a
redefinition of them.

## 0. Constraints this design must respect (read first)

1. **Headless is the primary mode, not an afterthought.** Limpet has no attached console
   (fleet instinct 1); it is driven by SSH/MCP from other nodes. A wizard that only works
   as a GUI is unusable on the real box. Therefore the wizard is specified as a **headless
   state machine with a thin GUI on top** (§5), not a GUI with a scripting bolt-on. The
   GUI and the CLI/MCP driver call the *same* engine; the GUI holds no logic the headless
   path lacks.
2. **The wizard orchestrates; it does not re-detect.** Hardware detection already belongs
   to the HAL udev inventory and the `list_hardware` tool
   (`design/wq3806_limpet_architecture.md:186-188`). The wizard reads that inventory; it
   does not open `/dev` or parse udev itself. One detection source of truth.
3. **Identification can be wrong, and the wizard must show that.** A VID:PID match is high
   confidence; a vision OCR guess on a chip marking is not. Every identification carries a
   confidence and its evidence, and no irreversible step (flashing, powering a probe) runs
   on a low-confidence guess without an explicit confirm — see the confidence gate in §4.3.
4. **Untrusted input from step one.** A plugged-in device and a dropped firmware image are
   both untrusted. The workspace the wizard creates applies the §5 hygiene of
   `design/wq3806_limpet_architecture.md:220-223` (artifact under `target/`, run tools by
   path argument, never execute from inside the artifact dir) from the moment it is created.

## 1. Entry points (what starts the wizard)

The wizard is one engine reachable three ways. All three produce the same `WizardSession`
(§3) and run the same state machine (§4).

| Trigger | Mode | Mechanism |
| --- | --- | --- |
| Operator plugs in a USB device | reactive | HAL udev hotplug event → inventory delta → wizard offered for the new device |
| Operator runs "New Target" | interactive | GUI button, or `limpet target new` CLI / `target_new` MCP tool |
| Operator supplies an image/dump | interactive | `limpet target new --artifact <path>` (no physical device; skips HAL steps, jumps to §4.2 on the file) |

Hotplug is an **offer, not an auto-run**: a newly attached device surfaces a pending
onboarding in the inventory and (GUI) a toast / (headless) a line in `target list
--pending`. It does not create a workspace or energize the device on its own — plugging a
device in must never, by itself, drive a probe or apply power. The operator (or an
explicitly authorized autonomous agent policy) starts the flow.

## 2. The flow (one sentence per stage)

```
detect → identify → suggest workflow → create workspace → begin analysis
```

1. **Detect** — read the HAL inventory; pick the device (or the supplied artifact).
2. **Identify** — resolve the device to a `TargetProfile`: SoC/family, interface mode,
   known vs unknown (§4.2).
3. **Suggest workflow** — map the profile to a recommended analysis plan and the Limpet
   tool/HAL plugins it needs (§4.4).
4. **Create workspace** — materialize the §5-isolated workspace, bind the identified
   hardware, write `manifest.json` identity (§4.5).
5. **Begin analysis** — hand off to the agent core loop
   (`design/wq3806_limpet_architecture.md:240-245`) with the first step pre-queued (§4.6).

Each stage is a committed transition in the session record, so an onboarding is itself
replayable and auditable, matching the per-workspace evidence discipline.

## 3. Data model

### 3.1 `TargetProfile` — the identification result

```jsonc
{
  "target_id": "mt7621-router-a7f3",     // slug + short hash, becomes workspace dir name
  "kind": "device | artifact",            // physical device vs supplied image/dump
  "identification": {
    "status": "known | probable | unknown",
    "soc": "MediaTek MT7621",            // null until identified
    "soc_family": "mips-24kec",          // arch hint for toolset selection; null if unknown
    "mode": "usb-serial | dfu | mass-storage | jtag | uart | unknown",
    "confidence": 0.0,                    // 0.0–1.0, see §4.3
    "evidence": [                         // every signal that fed the identification
      {"source": "usb-vid-pid", "value": "0e8d:0003", "db": "usb.ids", "weight": 0.9},
      {"source": "vision-ocr",  "value": "MT7621AT", "db": "wq3900", "weight": 0.5}
    ]
  },
  "hardware_binding": ["hw:mt76-serial", "hw:magewell-capture"],  // HAL plugin ids
  "operator_overrides": {}                // manual corrections, §4.3
}
```

`status` is derived, not stored independently: `known` when a VID:PID or manifest match
sets confidence ≥ 0.85; `probable` for 0.4–0.85 (e.g. a single vision OCR hit);
`unknown` below 0.4 or no signal. An operator override forces `known` and records who/what
set it in `operator_overrides`.

### 3.2 `WizardSession` — the state machine instance

`{session_id, stage, target_profile, suggested_workflow, workspace_path, log[]}` where
`stage` ∈ the §4 states and `log[]` appends one record per transition (timestamp, from,
to, actor). Persisted under `/var/limpet/sessions/<session_id>.json` so an interrupted
onboarding resumes rather than restarts — the same reason the fleet uses the busy key.

## 4. State machine

```
         ┌─────────┐   device    ┌──────────┐  match   ┌──────────┐
 start ─▶│ DETECT  │────────────▶│ IDENTIFY │─────────▶│ SUGGEST  │
         └─────────┘             └────┬─────┘  known    └────┬─────┘
                                      │ unknown/probable      │
                                      ▼                       ▼
                                 ┌──────────┐           ┌──────────┐
                                 │  ASSIST  │  confirm  │ WORKSPACE│
                                 │ (manual+ │──────────▶│  CREATE  │
                                 │  vision) │           └────┬─────┘
                                 └──────────┘                ▼
                                                        ┌──────────┐
                                                        │  ANALYZE │─▶ handoff to agent core
                                                        └──────────┘
```

Every state is re-enterable; `ASSIST` can loop (operator adds evidence, re-identifies)
until confidence clears the §4.3 gate or the operator forces an override.

### 4.1 DETECT

Read the HAL `list_hardware` inventory. For a hotplug entry, the target is the new device.
For `target new` with no device, prompt for / accept the `--artifact` path and enter
IDENTIFY against the file (static path: `file`, magic, `binwalk`-class signature scan —
the SoC-from-dump path already covered by the `firmware-extraction` skill). A device with
no recognized interface still proceeds — it becomes an `unknown` profile, not an error.

### 4.2 IDENTIFY — the known/unknown fork

Identification is additive: each source contributes weighted evidence to the profile, and
confidence is the combined result, not a single lookup.

**Known-device path (USB VID:PID).** Prior art (fleet-search 2026-10-14): the canonical
USB vendor/product map is `usb.ids` (linux-usb.org; mirrored at the-sz.com) — do not
hand-maintain a vendor table, ship/sync `usb.ids`. Resolution order:
1. **Limpet hardware manifest match.** If a `category=hardware` plugin's
   `hardware.detection.usb_vendor`/`usb_product`
   (`design/limpet/plugin-manifest.schema.json:102-110`) matches, that plugin *is* the
   device's HAL binding — highest-value hit, because it also yields capabilities. Weight
   0.9.
2. **`usb.ids` match.** Resolves vendor + product to human names for display and narrows
   SoC family (weight 0.6, a name is not a mode). Many RE targets expose a generic bridge
   (FTDI, CP210x) VID:PID — that identifies the *bridge*, not the target behind it, so a
   bridge match sets `mode` but leaves `soc` null and confidence capped at `probable`.
3. **Mode probes.** DFU descriptor, mass-storage, serial — cheap read-only USB descriptor
   reads that set `mode`. Never a write.

**Unknown-device path (manual + vision-assisted).** When VID:PID yields no SoC (bridge
only, no device, or an artifact with no signature), the wizard enters ASSIST:
- **Vision-assisted.** If a capture device is attached (Magewell/uvcvideo,
  `design/wq3806_limpet_architecture.md:194-196`) or the operator uploads a board photo,
  the wizard calls the **wq#3900 vision component-ID pipeline** to OCR chip silkscreen
  markings and return candidate part numbers with per-candidate confidence. The wizard
  consumes that as weighted `vision-ocr` evidence (§3.1); it does not re-implement OCR.
  The integration contract with wq#3900 is exactly: *in* = image/frame handle + optional
  ROI; *out* = `[{part_number, manufacturer, confidence, bbox}]`. Vision confidence is
  always treated as `probable` at best — OCR of a worn marking is a lead, not a fact.
- **Manual.** The operator types a known SoC / part number / mode. A manual entry is an
  authoritative override (§4.3) and sets `known`.

### 4.3 Confidence gate (the safety spine of identification)

- **≥ 0.85 (`known`)** — proceed to SUGGEST automatically.
- **0.4–0.85 (`probable`)** — proceed, but the suggested workflow is marked tentative and
  any step that **energizes the device** (applies probe power, drives JTAG/SWD, writes
  flash) requires an explicit operator confirm before it runs. Read-only steps (extract,
  static analysis, capture) run freely.
- **< 0.4 (`unknown`)** — the wizard will still create a workspace and begin *passive*
  analysis (dump, strings, signature scan), but refuses any active hardware action until
  identification improves or the operator overrides. This keeps instinct-3 autonomy intact
  for the safe (read-only) loop while gating the irreversible one on evidence, mirroring
  the agent-core autonomy boundary (`design/wq3806_limpet_architecture.md:246-249`).

An operator override always clears the gate and is logged with its actor.

### 4.4 SUGGEST workflow

Map `soc_family` / `kind` / `mode` to a recommended plan drawn from the Limpet tool
registry (`design/wq3806_limpet_architecture.md:240-245`). The suggestion names (a) the
analysis arc for the target class and (b) the specific enabled plugins each step needs;
plugins that are only `community` surface as "available after promotion", never silently
pulled (the §3 trust gate of the architecture doc). Examples:

| Profile | Suggested arc | Key plugins |
| --- | --- | --- |
| Router firmware image (mips/arm) | extract → static → emulate → verify | unblob, binwalk, Ghidra headless, QEMU system-mode |
| Live board with UART + JTAG | attach probe → dump flash → static → debug | OpenOCD debug-probe (HAL), Ghidra |
| USB gadget, unknown SoC | passive descriptor + capture → vision-ID → narrow | uvcvideo capture, wq#3900 vision |
| Display-only device (no debug port) | KVM capture → observe → infer | Magewell capture, agent vision loop |

The suggestion is a default, not a lock — the operator (GUI) or `--workflow <id>` (headless)
can choose another. Unknown targets get the passive arc as the floor.

### 4.5 WORKSPACE CREATE

Materialize the §5 workspace of `design/wq3806_limpet_architecture.md:206-218` at
`/var/limpet/workspaces/<target_id>/`: `target/` (artifact, by copy/bind — never executed
from), `evidence/`, `notes/`, `workspace.git` (initial commit = the onboarding record),
and `manifest.json` seeded from the `TargetProfile` (identity, hashes, `hardware_binding`,
confidence, evidence trail). The bound HAL plugins are reserved to this workspace so two
investigations cannot both drive the same probe. Creation is idempotent on `target_id`:
re-onboarding the same device resumes its workspace rather than forking a second.

### 4.6 ANALYZE handoff

Enqueue the first step of the suggested arc and transfer control to the agent core loop
(`design/wq3806_limpet_architecture.md:240-245`) scoped to this workspace and its bound
hardware. The wizard's job ends here; it has produced an open, identified, tooled
workspace with a queued first action. In headless/automation use the handoff can be
`--auto` (begin immediately) or default (stop after create, print the ready workspace path
for the caller to drive).

## 5. GUI and headless parity

One engine, two faces. The engine exposes the state machine as a library + MCP tools; the
GUI is a renderer over it.

- **Headless / agent-facing (primary).** CLI subcommands on the existing
  `limpet/cli.py` surface (which already uses an argparse subparser pattern,
  `limpet/cli.py:127-152`) and matching MCP tools, all non-interactive and JSON-emitting
  per the fleet CLI-for-agents standard:
  - `limpet target list [--pending]` — devices/artifacts and their onboarding state.
  - `limpet target new [--device <id>|--artifact <path>] [--json]` — start a session.
  - `limpet target identify <session> [--manual-soc X|--vision|--json]` — run/augment IDENTIFY.
  - `limpet target suggest <session> [--json]` — print the recommended workflow + plugins.
  - `limpet target create <session>` — WORKSPACE CREATE; prints the workspace path.
  - `limpet target begin <session> [--auto]` — ANALYZE handoff.
  - Every active-hardware action below the §4.3 gate requires `--confirm` to run headless;
    without it the command prints the blocking reason and exits non-zero, so an agent gets
    a failing, legible signal rather than a silent energize.
- **GUI.** A step-per-screen wizard (detect list → identification card with confidence +
  evidence → suggested-workflow picker → create → "open workspace") that calls those same
  engine operations. The confidence gate renders as a visible badge and an explicit confirm
  dialog for energizing steps; manual override is a form field. No branch of the flow is
  GUI-only.

Because both faces call one engine, a session started by hotplug in the GUI can be
finished from the CLI over SSH and vice versa — the `WizardSession` record (§3.2) is the
shared state.

## 6. Testability (how a build ticket proves this works)

The provisioning/build ticket that implements this spec should demonstrate, with an
acceptance probe that can fail (fleet doctrine):
1. **Known-device fork.** Feed a fixture USB descriptor whose VID:PID is in a Limpet
   hardware manifest → profile `status=known`, correct `hardware_binding`, confidence
   ≥ 0.85. Feed a bare FTDI-bridge VID:PID → `probable`, `soc=null`, `mode` set
   (proves the bridge-vs-target distinction of §4.2, a case drawn from *outside* the
   happy path).
2. **Unknown fork.** Feed a device with no manifest match and a stub vision result → the
   wizard enters ASSIST, records `vision-ocr` evidence, and holds at the §4.3 gate until a
   manual override or higher-confidence signal; assert no active-hardware command runs
   while `unknown`.
3. **Parity.** Drive one full onboarding through the CLI JSON interface and assert the same
   terminal `WizardSession` state a GUI run produces (same engine, so same record).
4. **Workspace hygiene.** Assert the created workspace matches the §5 layout and that the
   artifact lands under `target/` by path, never executed from.

The vision pieces of (2) stub against the wq#3900 contract (§4.2) so this wizard can be
built and tested before that pipeline lands.

## 7. Scope boundary

This document specifies the wizard. It does **not** design the vision pipeline (wq#3900),
the HAL detection internals (wq#3806 §4), the tool registry (wq#3806 §1), or the workspace
container runtime (wq#3806 §5) — it composes them. Building it inherits the wq#3806 §7
build order: the registry, meta-server, workspace manager, and HAL detection inventory
must exist first; the wizard is the operator-facing orchestration that sits on top once
they do.
