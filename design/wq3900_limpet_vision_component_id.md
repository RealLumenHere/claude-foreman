# wq#3900 — LIMPET vision-based component identification pipeline (design)

PCB photo in → structured component manifest out. Critical Gap #1 from the RE Tool
Catalog gap analysis: no open-source tool photographs a board and auto-identifies the
components by their markings. This document designs that pipeline for Limpet — capture →
vision model → marking decode/part lookup → manifest — as a Limpet plugin surfaced
through the Target Device Wizard (wq#3901, designed in parallel).

**Design-only.** Nothing here installs software or registers a tool. Every external tool
named below enters through the existing adoption gate (`TOOL_REGISTRY.md:4`, trust tiers
per `design/wq3806_limpet_architecture.md` §1.2). Companion artifacts in this branch:
`limpet/examples/component-id.toml` (a validating plugin manifest for the pipeline) and
the example-manifest test extension in `limpet/test_limpet_cli.py`.

## 0. Constraints and evidence posture (read first)

1. **Prior-art search ran degraded.** The mandatory fleet-search step was executed
   2026-10-14 but the web backend was fully down (all five SearXNG general engines
   suspended/CAPTCHA, no Brave fallback key — pre-existing wq#2595) and the GitHub
   backend had no token; reddit responded but empty (ISSUE-LOG row 1315). External tool
   facts below (Part-DB, FlashDetector, VLM choices, OCR engines) therefore come from
   model knowledge, are labeled **[verify-at-adoption]**, and MUST be re-verified during
   the TOOL_REGISTRY go/no-go before any install. The design is structured so a wrong
   external fact invalidates one stage's tool choice, never the pipeline shape.
2. **Fits the wq#3806 architecture, doesn't fork it.** Capture rides the HAL
   (`design/wq3806_limpet_architecture.md` §4: `/dev/video*` + V4L2 `capture_frame`),
   inference follows §6 (local Ollama, hard steps routable to fleet/cloud via LiteLLM),
   outputs land in the §5 workspace (`evidence/`, committed to `workspace.git`), and the
   plugin is trust-gated like every other tool. No new infrastructure classes.
3. **Memory budget is real.** The M70q (i5-14500, 32GB, iGPU-only, no dGPU) already
   budgets a resident ~9B coder model (§6). A vision model is **loaded on demand and
   evicted** (Ollama `keep_alive=0`), never co-resident by default; boards that defeat
   the local model route to a cloud vision model through LiteLLM. Do not design around
   two resident models.

## 1. Pipeline overview

```
[1 capture]      HAL camera → frames/           (multi-shot, glare-controlled)
[2 preprocess]   OpenCV → rectified board + component crop tiles
[3 read]         VLM (local Ollama multimodal; cloud fallback) + OCR cross-check
                 → per-crop {package, markings[], mfr-logo, type, confidence}
[4 resolve]      markings → part identity:
                 FlashDetector (flash P/Ns) → SMD code table (top-marks) →
                 Part-DB + info providers (full P/Ns, datasheets)
[5 manifest]     components.json in workspace evidence/ (schema §6), committed
[6 consume]      Target Device Wizard step: manifest → proposed RE toolset
```

Stages are independently replaceable: each writes its artifact into the workspace, and
every downstream stage consumes only the prior stage's artifact. A run is replayable
from any stage's saved inputs.

## 2. Stage 1 — capture

- **Device.** A UVC camera is the primary input: any USB microscope/macro camera
  enumerating as `/dev/video*` works through the existing HAL `capture_frame` path. The
  Magewell HDMI capture the ticket names is the *same V4L2 path* and is used when the
  imaging source outputs HDMI (bench inspection camera); it is not required. Operators
  can also drop photos from a phone into the workspace — the pipeline takes files, the
  camera is a convenience, so the pipeline is usable before any camera is purchased.
- **Protocol.** Component markings are the failure point of every downstream stage, and
  the enemy is glare on laser-etched IC tops. The capture step therefore takes a
  **burst**: ≥3 frames per board side at different exposures (and lighting angles if the
  operator can vary them), full-board plus operator-guided close-ups. All frames are
  content-hashed into `workspace/target/frames/`; the manifest records which frame each
  reading came from (provenance, §6).
- **Hardware plugin.** The camera is a `kind = "hardware"` plugin (usb_ids detection,
  like `limpet/examples/hackrf.toml`); the pipeline itself is the `kind = "tool"` plugin
  `component-id` (`limpet/examples/component-id.toml`) that depends on it only
  optionally — file input must work with zero hardware attached.

## 3. Stage 2 — preprocess (OpenCV, classical only in v1)

Board rectification (perspective correction from board outline), CLAHE contrast
enhancement, exposure fusion of the burst, then tiling into overlapping crops sized so
an IC's marking fills a meaningful fraction of the crop (VLMs and OCR both fail on
tiny text in a wide field).

**Deliberate v1 choice: no learned component detector.** Options considered:
(a) train/adopt a YOLO-class detector on PCB datasets (FICS-PCB, PCB-DSLR
**[verify-at-adoption]**), (b) classical contour/color segmentation, (c) let the VLM
ground components itself on overlapping tiles. v1 ships (c) with (b) as a cheap tile
prioritizer: it requires zero new model assets, and modern VLMs localize well enough to
return per-component bounding boxes in a tile. A detector is a v2 optimization slot —
the Coral TPU already reserved in the architecture (§4: "PCB image triage") is exactly
where an int8 detector would run — but it is an accuracy/cost optimization, not a
dependency. This keeps the gap-closing milestone small.

## 4. Stage 3 — read (the vision core)

**Two tracks, cross-checked:**

- **Track A — VLM (primary).** A local multimodal model served by Ollama reads each
  crop and returns structured JSON per component: `bbox`, `package` (SOIC-8, QFP-64,
  0603…), `markings[]` (verbatim lines), `mfr_logo` (if recognized), `type_guess`
  (MCU/flash/PMIC/passive/connector/debug-header…), `confidence`. Candidate local
  models **[verify-at-adoption]**: qwen2.5-vl:7b, minicpm-v, llama3.2-vision:11b —
  chosen at provisioning against the fleet model-selection standard
  (`research/WQ3798_M80S_LLM_MODEL_SELECTION.md`), not hard-coded here; quantized 7-8B
  VLMs fit the 32GB box when loaded on demand (§0.3). A VLM is the right core because
  IC tops are short, stylized, multi-line, logo-bearing text — a reader that also
  *interprets* (logo→manufacturer, layout→package) does what plain OCR cannot.
- **Track B — deterministic OCR (cross-check).** PaddleOCR (preferred) or Tesseract
  **[verify-at-adoption]** runs on the same enhanced crops. Where Track B's text and
  Track A's `markings[]` agree, confidence is boosted; where they disagree, the
  component is flagged `needs_review` rather than silently trusting either. OCR alone
  is known-weak on laser-etched marks; it is kept precisely because it fails
  *differently* from a VLM (no hallucinated plausible part numbers).
- **Escalation.** Components still low-confidence after both tracks are batched into
  one cloud-vision call (Claude vision via the LiteLLM route the architecture already
  specifies, §6) — opt-in per workspace, because a workspace may be network-isolated
  (§5 containment) or the board confidential. The manifest records which model produced
  each reading.

**Hallucination control (load-bearing).** The VLM prompt requires verbatim transcription
with explicit `?` for unreadable glyphs and forbids completing a part number from
knowledge; any marking that Track B cannot at least partially corroborate and that stage
4 cannot resolve is kept as raw text with `resolved: null` — never auto-upgraded to a
guessed identity. A wrong part ID that looks authoritative is worse for an RE workflow
than an honest unknown (it sends the operator to the wrong datasheet).

## 5. Stage 4 — resolve (markings → part identity)

Three resolvers, tried in order of specificity; all offline-first:

1. **FlashDetector** **[verify-at-adoption]** (iTXTech/FlashDetector): decodes NAND/eMMC
   flash part numbers (Samsung/Micron/Toshiba/Hynix grammar) into geometry and
   interface. Flash parts are the single highest-value identification for RE (dump
   target), which earns the dedicated decoder.
2. **SMD top-mark table.** Small packages carry 2-5 char codes, not part numbers (the
   classic "SMD codebook" problem). A local lookup table (code, package) → candidate
   parts, shipped as plugin data; multiple candidates are all listed with the package
   as disambiguator. **[verify-at-adoption]** for whichever dataset is cleanest to
   vendor; the table format is ours either way.
3. **Part-DB** **[verify-at-adoption]** (Part-DB/Part-DB-server): self-hosted parts
   database with a REST API and built-in info providers (Octopart/Digi-Key/Mouser/LCSC).
   Full part numbers resolve here; hits are cached locally so repeat boards work
   offline, and provider calls are an explicit per-workspace opt-in (same
   network-isolation rule as the cloud VLM). Part-DB doubles as the operator-facing
   inventory of every part Limpet has ever identified — fleet-wide memory of boards.

Each resolver adoption is its own go/no-go row; the pipeline degrades gracefully to
"markings transcribed, unresolved" if none are installed yet.

## 6. Stage 5 — component manifest (the deliverable)

`workspace/evidence/components.json`, committed to `workspace.git`:

```json
{
  "schema": "limpet.component-manifest/1",
  "board": {"workspace": "<target-id>", "frames": [{"path": "...", "sha256": "..."}]},
  "components": [{
    "id": "U3",
    "bbox": {"frame": "frames/f2.png", "xywh": [812, 440, 96, 96]},
    "package": "SOIC-8",
    "markings": ["25Q64JVSIQ", "W9"],
    "type": "spi-nor-flash",
    "part": {"mpn": "W25Q64JVSSIQ", "manufacturer": "Winbond",
              "resolver": "flashdetector", "datasheet": "<url-or-null>"},
    "confidence": 0.93,
    "needs_review": false,
    "read_by": {"vlm": "qwen2.5-vl:7b", "ocr_agree": true},
    "re_notes": ["8MB SPI NOR — dump candidate", "pin 1 at marked corner"]
  }],
  "unresolved": [{"id": "U7", "markings": ["A4?2", "..."] }],
  "run": {"pipeline_version": "...", "timestamp": "...", "escalated_to_cloud": false}
}
```

Properties that matter: every reading has **provenance** (frame hash + crop bbox + which
model), unknowns are **first-class** (`unresolved`, `needs_review`), and `re_notes`
carries the RE-relevant interpretation (dump candidates, debug headers, MCU family) that
the Wizard consumes. The schema version is in-band so the Wizard can reject manifests it
doesn't understand.

## 7. Integration

- **Limpet plugin.** `component-id` (tool kind — manifest at
  `limpet/examples/component-id.toml`) exposes via the MCP meta-server:
  `identify_components(workspace, frames?)` (full pipeline),
  `read_marking(crop_path)` (stage 3 only), `lookup_part(marking, package?)` (stage 4
  only). The split matters: the agent core can re-run just the resolver when an operator
  corrects a transcription. Health check = pipeline self-test on a committed fixture
  image (so health can actually fail, not just "binary exists").
- **Target Device Wizard (wq#3901, in flight).** Contract only, no coupling to that
  design's internals: the Wizard invokes `identify_components` as its "what is on this
  board?" step and receives the §6 manifest path. From `type`/`part` fields it proposes
  the toolset (SPI flash found → dumper/programmer plugin; ARM MCU found → OpenOCD
  config candidates; UART/JTAG header found → probe hookup step). Anything in
  `unresolved`/`needs_review` becomes an operator-confirmation screen, not a silent
  skip. If wq#3901 lands a different step API, only this paragraph's call-site changes.
- **Trust tiers.** The pipeline plugin itself is fleet-authored (`core` once
  implemented). FlashDetector, Part-DB, PaddleOCR, and any VLM weights are external
  adoptions: authored at `community`, promoted per `TOOL_REGISTRY.md:40-46` after their
  **[verify-at-adoption]** checks. The pipeline must run (degraded) with zero external
  resolvers enabled — that is what makes the tier gate honest rather than decorative.

## 8. Evaluation plan (for the implementation ticket's acceptance)

A **golden-board fixture**: one physical board photographed per the §2 protocol, with a
hand-labeled reference manifest, committed (images + labels) as the plugin's test
fixture. Metrics, measured by a script not by eyeballing:

- component recall (found / present), marking character accuracy (per-line edit
  distance), resolved-part precision (resolved-and-correct / resolved — this is the
  hallucination metric and the one that must be strict), unresolved honesty (a part the
  pipeline couldn't read must appear in `unresolved`, never with a fabricated identity).

Pass bar is set by the implementation ticket against a first baseline run; this design
fixes the *metrics*, not the numbers. The fixture also powers the plugin health check
(§7) and makes every future model swap (local VLM upgrade, detector addition) a measured
A/B instead of vibes.

## 9. Build order

1. Manifest schema + pipeline skeleton on **file input** (no camera): preprocess → VLM
   (fleet-routed, since Limpet isn't provisioned — wq#3811 blocked) → manifest. Golden
   fixture + metrics script in the same PR.
2. Resolver chain behind the go/no-go gate: FlashDetector, SMD table, Part-DB — each its
   own TOOL_REGISTRY row, pipeline functional before any of them.
3. OCR cross-check track + `needs_review` flow.
4. HAL camera capture (burst protocol) once hardware exists; Magewell path is free.
5. Wizard step wiring against wq#3901's landed contract.
6. v2 slots: local VLM residency tuning, Coral int8 component detector, operator
   correction feedback into the SMD table.

## 10. Open items

- wq#2595 (Brave key) blocks clean re-verification of every **[verify-at-adoption]**
  fact; the adoption go/no-gos should not start before fleet-search web works.
- Camera hardware selection (USB microscope vs. Magewell+bench camera) is a purchase —
  physical-task queue, not a gate: build order steps 1-3 need no camera.
- Whether Part-DB runs on Limpet itself or a fleet host (it is a PHP/Symfony service) —
  decide at adoption; the pipeline only sees its REST URL.
