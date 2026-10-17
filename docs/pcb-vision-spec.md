# wq#3960 — LIMPET PCB component vision pipeline

Photograph a populated board, get back a structured component manifest: every IC, flash,
passive, and connector located, its markings read, and a candidate part identity resolved
— offline, on CPU, on the minimum-spec appliance. This is Limpet differentiator #3 (no
open-source tool photographs a board and auto-identifies its components by their
markings), and it is the "what is on this board?" step the Target Device Wizard
(`docs/target-wizard-spec.md`, wq#3901) calls before it proposes a toolset.

This spec writes up the **staged detector/OCR pipeline** researched in the
2026-10-15 Thor docs digest (section C). It **refines wq#3900**
(`design/wq3900_limpet_vision_component_id.md`), which sketched a VLM-first,
"no learned detector in v1" pipeline: measurement since then favours a classical
object-detection front end (YOLO) with deterministic OCR (PaddleOCR) over a resident
multimodal VLM, because the VLM path cannot meet the latency/memory budget of the
**16GB minimum-spec floor** on CPU. The VLM is retained only as an opt-in escalation for
components both the detector and OCR leave low-confidence (§4.4). Where this spec and
wq#3900 disagree on stage internals, this spec wins; where they agree on posture
(offline-first, honest-unknown, workspace-committed artifacts, trust-gated adoption),
wq#3900 is the origin and still reads well.

It builds on the Limpet architecture (`design/wq3806_limpet_architecture.md`: HAL capture
§4, workspace/evidence §5, MCP meta-server) and does not fork it. It is **design-and-budget
only**: it installs nothing, registers no MCP server, and every external model or tool
named below enters through the existing adoption gate (`TOOL_REGISTRY.md`), labelled
**[verify-at-adoption]** where the fact comes from model knowledge rather than a committed
artifact.

## 0. Constraints this design must respect (read first)

1. **The 16GB floor is the design target, not 32GB.** The source research benchmarked on
   an i5-14500T with **32GB** RAM and reported a ~0.8–2 s per-board figure. That 32GB is
   **not** the Limpet minimum. The minimum spec stays **8th-gen Intel / Zen 2-equivalent
   or newer, 16GB RAM, 256GB SSD** (Tier 3 "RECOMMENDED" in `docs/hardware-tiers-spec.md`
   §2). The M70q dev/test rig (same i5-14500T, **16GB RAM** pending upgrade, 512GB SSD)
   sits **at the 16GB floor**, so it is the validation rig, not a headroom box. The
   pipeline's resource budget (YOLO **and** PaddleOCR loaded at once) is validated against
   16GB in §7, and the latency figure is re-derived on the actual M70q rather than trusted
   from the 32GB benchmark.

2. **CPU-only inference.** The minimum-spec box has an iGPU and no discrete GPU; several
   supported Tier 3 parts have no usable GPU-compute path at all. Every model therefore
   ships as an **OpenVINO INT8** export and runs on the CPU plugin. No stage may require
   CUDA/ROCm; GPU, where present, is an optional accelerator the same export can target,
   never a dependency.

3. **Headless.** Limpet has no attached console (fleet instinct 1); it is driven by SSH/MCP
   from other nodes. The pipeline is a set of MCP tools and a CLI over one engine (§6); any
   GUI is a thin client of that engine, never the only way in.

4. **Offline-first, opt-in network.** The whole pipeline — detection, OCR, and marking
   resolution — runs with **zero network**. Online resolvers (Part-DB info providers,
   cloud VLM escalation) are an explicit per-workspace opt-in, because a workspace may be
   network-isolated or the board confidential. A fresh appliance identifies a board it has
   never seen, with no account anywhere.

5. **Untrusted input from frame one.** A dropped photo and an attached camera are both
   untrusted input. Frames land under the workspace `target/` tree and are processed by
   path argument; nothing is executed from inside the artifact directory
   (`design/wq3806_limpet_architecture.md` §5 hygiene).

6. **Honest unknown over confident guess.** A wrong part ID that looks authoritative sends
   the operator to the wrong datasheet — worse for an RE workflow than an admitted unknown.
   Every reading carries provenance and confidence; a marking that OCR cannot corroborate
   and the resolver cannot match is kept as raw text with `part: null`, never auto-upgraded
   to a guessed identity (§4.3, §5.4).

## 1. Pipeline overview

```
[1 capture]       HAL camera / dropped photos → target/frames/   (burst, glare-controlled)
[2 preprocess]    OpenCV → rectified board + exposure-fused, tiled crops
[3 detect]        YOLO (board OBB + component OBB, OpenVINO INT8) → per-component boxes
[4 read]          PaddleOCR PP-OCRv4 (primary) + Tesseract (secondary cross-check)
                  → per-box {markings[], package-hint, confidence}
[5 resolve]       markings → part identity via the 3-table SMD DB (§5) + opt-in Part-DB
[6 manifest]      components.json in workspace evidence/ (schema §5.4), committed
[7 connectivity]  (opt-in) ReTrace net/trace reconstruction → connectivity.json, verified
```

Each stage writes its artifact into the workspace and consumes only the prior stage's
artifact, so a run is **replayable from any stage** and each stage is independently
swappable (a detector upgrade or an OCR engine swap is an A/B against a fixture, §8, not a
rewrite). Stages 3–5 are where the accuracy targets live (§9).

## 2. Stage 1 — capture

Unchanged in intent from wq#3900 §2: a UVC camera enumerating as `/dev/video*` is the
primary input through the HAL `capture_frame` path; a Magewell HDMI capture is the same
V4L2 path when the imaging source outputs HDMI; and operators can drop phone photos into
the workspace, so the pipeline is usable before any camera is bought. The capture protocol
takes a **burst** — ≥3 frames per board side at varied exposure/lighting to beat glare on
laser-etched IC tops — and content-hashes every frame into `target/frames/`. The manifest
records which frame each reading came from.

## 3. Stage 2 — preprocess (OpenCV, classical)

Board rectification (perspective correction from the board outline), CLAHE contrast
enhancement, exposure fusion of the burst, and tiling into overlapping crops sized so an
IC marking fills a usable fraction of the frame. This is classical CV only — no model — and
produces both the full rectified board (for the board-detection head) and the crop tiles
(for the component head and OCR).

## 4. Stages 3–4 — detect and read (the vision core)

The load-bearing change from wq#3900: component localisation moves from "let the VLM ground
components on tiles" to a **trained object detector**, and the reader moves from "VLM
primary" to **deterministic OCR primary**. Both run on CPU as OpenVINO INT8.

### 4.1 Detection — YOLO

- **Board detection.** A YOLO head locates the board within the rectified frame and emits
  an **oriented bounding box** (OBB), because boards are rarely photographed axis-aligned.
  Target: IoU ≥ 0.95 against the labelled board outline (§9).
- **Component detection.** A second head (or a multi-class single model) locates each
  component and emits an OBB per part — OBB matters here because SMD parts sit at arbitrary
  rotations and an axis-aligned box over a rotated SOIC swallows neighbours. Output is one
  box + coarse class (IC / flash / passive / connector / debug-header) per component, feeding
  the per-crop OCR. Target: precision and recall ≥ 0.90.
- **Model choice [verify-at-adoption].** Candidate backbones are **YOLOv8n/s** or
  **YOLOv11-OBB**; the OBB capability is the deciding feature, with `n`/`s` sizing chosen to
  hold the §7 budget. The weights are a fleet-trained/adopted asset (PCB datasets — FICS-PCB,
  PCB-DSLR **[verify-at-adoption]**), exported to OpenVINO INT8 at provisioning. The choice
  between v8 and v11-OBB is settled against the §8 fixture and the §7 budget at adoption, not
  hard-coded here.

### 4.2 Reading — PaddleOCR primary, Tesseract secondary

- **Primary: PaddleOCR PP-OCRv4 [verify-at-adoption].** Run on each component crop; it
  returns text lines with per-line confidence. PP-OCRv4's mobile detection+recognition
  models export to OpenVINO INT8 and are the primary OCR because they handle the rotated,
  multi-line, low-contrast text on IC tops far better than a general OCR.
- **Secondary: Tesseract [verify-at-adoption].** A second pass on the same enhanced crops.
  Its value is that it **fails differently** from PaddleOCR (it does not invent plausible
  part numbers). Where the two engines' text agrees, confidence is boosted; where they
  disagree, the component is flagged `needs_review` rather than silently trusting either.
- Target: OCR **exact-match** on the primary marking line ≥ 0.80 against the labelled
  fixture (§9).

### 4.3 Hallucination control (load-bearing)

OCR output is verbatim transcription with explicit `?` for unreadable glyphs; neither engine
completes a part number from "knowledge". A marking that the secondary engine cannot at
least partially corroborate and that stage 5 cannot resolve is kept as raw text with
`part: null` — never auto-upgraded to a guessed identity (§0.6).

### 4.4 Escalation (opt-in, not resident)

Components still low-confidence after detect+OCR are batched into **one** cloud-vision call
(Claude vision via the LiteLLM route the architecture already specifies) — opt-in per
workspace, because a workspace may be network-isolated. The local VLM path from wq#3900 is
**not resident by default** on the 16GB floor (§7); if a box has the headroom it may serve a
local multimodal model on demand (`keep_alive=0`), but the minimum-spec design does not
assume it.

## 5. Stage 5 — resolve, and the SMD marking database

### 5.1 A dedicated 3-table SQLite DB, distinct from the SoC-exploit DB

Marking resolution is backed by a local SQLite database shipped with the image, **separate
from `soc-exploit-db`** (`ops/wq3942-limpet-exploit-db-a1/device_exploit_db.py`, a single
`devices` table keyed on `soc_family` at
`/var/lib/limpet/exploit-db/device-exploits.db`). The two answer different questions — "what
known exploit targets this SoC?" versus "what part carries this top-mark?" — key on
different columns, and update on different cadences, so they are different databases, not two
tables in one. The PCB DB lives at `/var/lib/limpet/pcb-vision/marking-db.sqlite` with three
tables:

| Table        | Keyed on                      | Holds                                                                 |
|--------------|-------------------------------|-----------------------------------------------------------------------|
| `marking`    | `top_mark` + `package`        | SMD top-mark code → one or more candidate `component` rows (2–5 char codes are ambiguous, so package disambiguates) |
| `package`    | `package` (SOIC-8, QFP-64, 0603…) | pin count, body geometry, footprint family — lets detection's package-hint prune `marking` candidates |
| `component`  | `mpn`                         | canonical part: manufacturer, type (`spi-nor-flash`, `mcu`, `pmic`…), datasheet URL, RE notes |

`marking` → `component` is many-to-many through the candidate relation, because one top-mark
maps to several MPNs that only the package (and sometimes the board context) tells apart.
The DB is built and rebuilt idempotently from a committed seed, the same posture as the
exploit DB's `build_db` (drop-and-recreate, never append-duplicate).

### 5.2 Resolver order (offline-first)

Markings are resolved most-specific first, every step offline-capable:

1. **FlashDetector [verify-at-adoption]** — decodes NAND/eMMC/SPI-NOR part numbers into
   geometry and interface. Flash is the highest-value RE identification (the dump target), so
   it gets a dedicated grammar decoder ahead of the generic table.
2. **SMD top-mark table (§5.1)** — the local `marking`/`package` lookup, fully offline,
   returns all candidates with the package as disambiguator.
3. **Part-DB [verify-at-adoption]** — self-hosted parts DB with info providers
   (Octopart/Digi-Key/Mouser/LCSC). Full part numbers resolve here; hits are cached into the
   local `component` table so repeat boards work offline, and provider calls are an explicit
   per-workspace opt-in. Part-DB doubles as the fleet-wide inventory of every part Limpet has
   identified.

The pipeline degrades gracefully to "markings transcribed, unresolved" when no resolver is
installed; each resolver is its own adoption go/no-go row.

### 5.3 Connectivity (stage 7, opt-in) — ReTrace

Where the board photos support it, **ReTrace [verify-at-adoption]** (PCB trace/net
reconstruction) runs as an opt-in stage producing a `connectivity.json` net list, verified
against the component manifest (every net endpoint must land on a detected component pad).
This is the input that turns a component list into a wiring picture for the Wizard; it is
opt-in because it is compute-heavy and not every investigation needs it.

### 5.4 The deliverable — component manifest

`workspace/evidence/components.json`, committed to `workspace.git`, schema
`limpet.component-manifest/1` (unchanged from wq#3900 §6): every component carries its
bounding box and source frame hash (provenance), its `markings[]`, a resolved `part` or an
explicit `null`, a `confidence`, a `needs_review` flag, `read_by` (which OCR engines agreed),
and `re_notes`. Unknowns are first-class: an `unresolved[]` array and `needs_review` on
individual components. The schema version is in-band so the Wizard can reject a manifest it
does not understand.

## 6. MCP surface — nine tools

The pipeline exposes nine tools through the MCP meta-server under the `pcb.*` namespace, one
engine behind all of them (the CLI drives the same engine). The split lets the agent re-run a
single stage — e.g. re-resolve after an operator corrects a transcription — without
re-photographing the board:

| Tool                      | Stage | Does                                                            |
|---------------------------|-------|----------------------------------------------------------------|
| `pcb.capture.import`      | 1     | ingest frames (camera burst or dropped files) into `target/frames/`, content-hashed |
| `pcb.capture.enhance`     | 2     | OpenCV rectify / CLAHE / exposure-fuse / tile → crop set        |
| `pcb.detect.board`        | 3     | YOLO board OBB (IoU gate)                                       |
| `pcb.detect.components`   | 3     | YOLO component OBBs + coarse class (P/R gate)                   |
| `pcb.read.markings`       | 4     | PaddleOCR + Tesseract cross-check per crop (exact-match gate)   |
| `pcb.resolve.part`        | 5     | marking → candidate parts via FlashDetector / SMD DB / Part-DB (top-3 gate) |
| `pcb.manifest.build`      | 6     | assemble + validate `components.json`, commit to workspace      |
| `pcb.manifest.export`     | 6     | render the manifest into the reporting system (`docs/reporting-spec.md`) |
| `pcb.connectivity.verify` | 7     | ReTrace net reconstruction + verify endpoints against manifest  |

The plugin ships as the `component-id` tool manifest (`limpet/examples/component-id.toml`,
wq#3900) extended to this surface; its health check remains a pipeline self-test against the
committed golden-board fixture (§8), so health can genuinely fail rather than merely confirm a
binary exists.

## 7. Resource budget — validated against the 16GB floor

This is the section the ticket's critical correction demands: the pipeline loads **YOLO and
PaddleOCR simultaneously** (detection feeds OCR within one board run), and that combined
budget is validated against **16GB**, not the research's 32GB.

**Memory accounting (CPU-only, OpenVINO INT8), peak during one board run:**

| Resident item                                   | Budget (to confirm on M70q) |
|-------------------------------------------------|-----------------------------|
| Base OS (Xubuntu, headless) + Limpet services   | ~2.0 GB                     |
| Python + OpenVINO CPU runtime                   | ~0.5–1.0 GB                 |
| YOLO (v8n/s or v11-OBB) INT8 working set         | ~0.3–0.7 GB                 |
| PaddleOCR PP-OCRv4 (det+rec+cls) INT8 working set | ~1.0–2.0 GB               |
| Tesseract (runs after Paddle, not co-resident)  | ~0.1 GB                     |
| Burst frame + crop buffers (high-res)           | ~0.5–1.0 GB                 |
| **System peak (incl. OS)**                      | **~4.4–6.8 GB**             |

That peak sits comfortably inside 16GB — **with one hard condition**: the resident ~9B coder
model that wq#3900 §0.3 budgeted on a 32GB box **must be evicted** (`keep_alive=0`) while the
vision pipeline runs. A 9B Q4 model is ~5–6 GB; co-resident with a ~5–7 GB vision peak and a
~2 GB OS it would push a 16GB box into swap, and swap is where the latency figure dies (§7.1).
On 32GB the research never hit this wall, which is exactly why its budget cannot be adopted
unexamined. **Design rule: on the 16GB floor the coder model and the vision stack are
mutually exclusive residents**, scheduled by the engine, never co-loaded. The local VLM
escalation of §4.4 is for this reason not resident by default.

### 7.1 Latency, re-derived on the actual M70q

The research's ~0.8–2 s per board was measured on an i5-14500T/32GB. The M70q is the **same
i5-14500T**, but its channel count does **not** match the benchmark box and latency must not be
assumed to transfer. CPU inference latency here is compute- and memory-**bandwidth**-bound, and
bandwidth is a function of memory generation/channels, not capacity
(`docs/hardware-tiers-spec.md` §3: token generation is memory-bandwidth-bound, and
same-memory parts generate at the same rate) — but Farida's `dmidecode -t memory` (2026-10-16)
shows the actual 16GB M70q rig runs **one** 16GB DDR5 SODIMM (Controller0 is empty): single
channel. The research-ingestion summary records only "32GB" for the benchmark box and never
states its DIMM count, so we cannot confirm it was also single-channel; if it ran 2x16GB, it had
roughly double the bandwidth of this rig, and §7.1's own logic says that bandwidth gap could move
the latency figure. **Capacity budget (§7) is unaffected and holds; the latency figure is not
assumed to transfer** until it is **confirmed by a measured run on the actual 16GB M70q**
(milestone M3, §9) rather than asserted from the 32GB benchmark.

Hardware note: filling the empty slot with a second matched 16GB SODIMM would give the M70q
32GB **and** dual channel; swapping to a single 32GB stick would leave it at 32GB but still
single-channel.

## 8. Evaluation — the golden-board fixture

A **golden-board fixture** (one physical board photographed per the §2 burst protocol, with a
hand-labelled reference manifest and labelled board/component OBBs) is committed as the
plugin's test fixture, pinned so the acceptance probe is re-runnable. A metrics script — not
eyeballing — measures the §9 targets, and the same fixture powers the plugin health check
(§6), so every future model swap (YOLO v8↔v11, an OCR upgrade, a detector added later) is a
measured A/B rather than a vibe. The honesty metric is strictest: any part the pipeline could
not read **must** appear in `unresolved[]`, never with a fabricated identity.

## 9. Milestones and accuracy targets

Per-stage targets, each measured against the §8 fixture by script:

| Stage target                          | Gate                  |
|---------------------------------------|-----------------------|
| Board detection                       | IoU ≥ 0.95            |
| Component detection                   | precision & recall ≥ 0.90 |
| OCR marking read (exact-match)        | ≥ 0.80                |
| Resolver (correct candidate in top 3) | ≥ 0.90                |

**M1 — skeleton on file input.** Manifest schema + stages 2→3→4→6 on dropped photos (no
camera): preprocess, YOLO detect, PaddleOCR read, build manifest. Golden fixture + metrics
script land in the same PR. Detection and OCR gates measured for the first time here.

**M2 — resolver chain.** FlashDetector, the 3-table SMD DB (§5.1), and Part-DB behind the
adoption gate — each its own TOOL_REGISTRY row, pipeline functional (degraded) before any of
them. Resolver top-3 gate measured.

**M3 — budget + latency validation on the 16GB M70q.** Run the full stack on the actual
M70q at the 16GB floor, confirm the §7 memory peak and the coder-model eviction rule, and
**measure** per-board latency against the re-derived ~0.8–2 s target (§7.1). This milestone
exists specifically because the 32GB benchmark cannot be trusted for the 16GB floor.

**M4 — connectivity + Wizard wiring.** ReTrace connectivity stage (§5.3) and the Tesseract
cross-check / `needs_review` flow; wire `pcb.*` into the Target Device Wizard's
"what is on this board?" step against wq#3901's landed contract. Camera burst capture (HAL)
folds in once hardware exists — the Magewell path is free (§2).

## 10. Trust tiers and open items

- **Trust.** The pipeline plugin is fleet-authored (`core` once implemented). YOLO weights,
  PaddleOCR, Tesseract, FlashDetector, Part-DB, and ReTrace are external adoptions — authored
  `community`, promoted per `TOOL_REGISTRY.md` after their **[verify-at-adoption]** checks. The
  pipeline must run (degraded) with zero external resolvers enabled; that is what makes the
  tier gate honest rather than decorative.
- **Prior-art caveat.** Several external facts above (PP-OCRv4 OpenVINO export sizes,
  YOLOv11-OBB specifics, FlashDetector/Part-DB/ReTrace capabilities, PCB dataset licences)
  carry **[verify-at-adoption]** and must be re-verified at the go/no-go; wq#2595 (fleet-search
  web backend) blocked a clean re-verification at design time. The budget and latency numbers
  in §7 are engineering estimates to be **replaced by M3's measured values**.
- **Camera hardware** (USB microscope vs. Magewell + bench camera) is a purchase — a
  physical-task queue item, not a gate: M1–M3 need no camera.
- **Part-DB host** (on Limpet itself vs. a fleet host — it is a PHP/Symfony service) is decided
  at adoption; the pipeline only sees its REST URL.
