# wq#3922 — Limpet vision pipeline: webcam + HDMI capture + local VLMs

Two capture paths into one vision core: **(1)** a USB webcam reads PCB chip markings for
component ID, **(2)** an HDMI capture card lets the agent see a target device's own screen.
This research picks the model, sizes it to the box, and specifies the V4L2/HAL plumbing.
It is the device-and-model deep-dive under the pipeline **design** already landed in
`design/wq3900_limpet_vision_component_id.md` (USE CASE 1) and the HAL contract in
`design/wq3806_limpet_architecture.md` §4 (both use cases). It **blocks** the Target Device
Wizard vision path (`docs/target-wizard-spec.md`).

Nothing here installs software or touches hardware. Every external-tool/device fact is
**[verify-at-adoption]** — re-confirm at the `TOOL_REGISTRY.md` go/no-go, and (for devices)
against the real unit on the real box, before any provisioning.

## 0. Evidence posture (read first)

**Prior-art search ran degraded — same wall as wq#3900/wq#3921.** The mandatory
`fleet-search` step was executed 2026-10-15:

- `fleet-search web "Ollama multimodal vision models Qwen2.5-VL moondream MiniCPM-V …"`
  → `ok:false`: all general engines suspended/CAPTCHA (brave, duckduckgo, google cse,
  startpage), no Brave fallback key (**wq#2595, OPEN**).
- `fleet-search reddit "USB HDMI capture card UVC v4l2 linux chipset MS2109 MS2130"`
  → `ok:true` but zero results (backend up, corpus empty — same as wq#3900 §0.1).

So the external facts below (model sizes/scores, device VID:PIDs, chipset behaviour) come
from **model knowledge (cutoff 2026-01)**, carry **[verify-at-adoption]**, and are
structured so a wrong external fact invalidates one row of a table, never the pipeline
shape. This is not fabricated-evidence (instinct 8): wq#2595 is a real pre-existing OPEN
fleet issue, not a value this ticket invented.

**Hardware target.** M70q Gen 5 class — Intel i5-14500(T), **32 GB RAM, iGPU-only, no
dGPU**, plus the reserved Coral TPU for triage only (`design/wq3806_limpet_architecture.md`
§4 line 200, §6 line 234). "Tier 3-4 / 14-32 GB model budget" from the ticket is read as
*resident VRAM-equivalent* — on an iGPU-only box that is system RAM shared with Ollama.

---

## USE CASE 1 — PCB component identification (webcam → markings → part)

### 1a. Local multimodal VLMs available on Ollama  [verify-at-adoption]

| Model (Ollama tag) | Params | ~q4 size on disk | OCR strength | Notes |
|---|---|---|---|---|
| `moondream` (2) | ~1.8B | ~1.7 GB | weak | tiny/fast; scene gist, not dense small text |
| `llava:7b` / `llava:13b` (1.6) | 7/13B | ~4.5 / 8 GB | weak-moderate | older arch, superseded for OCR |
| `llava-llama3:8b` | 8B | ~5.5 GB | moderate | better grounding than llava 1.5 |
| `llama3.2-vision:11b` | 11B | ~7.9 GB | moderate | solid general VQA, OCR mid-pack |
| `minicpm-v` (2.6) | 8B | ~5.5 GB | **strong** | OCRBench leader among small VLMs; multi-image |
| `qwen2.5-vl:7b` | 7B | ~6 GB | **strong** | best OCR+bbox+JSON at this size; dynamic-res |
| `qwen2.5-vl:3b` | 3B | ~3.2 GB | good | cheaper fallback if 7B latency hurts |
| `qwen2.5-vl:32b` | 32B | ~20 GB | strongest | fits budget but CPU-slow; escalation only |

### 1b. Which fit the Tier 3-4 (14-32 GB) budget

All of the **3B-11B** rows fit with comfortable headroom when loaded on demand (Ollama
keeps one model resident, unloads on idle — matches the "cannot hold every backend
resident" constraint, `design/wq3806_limpet_architecture.md` line 141). `qwen2.5-vl:32b`
q4 (~20 GB) *fits the budget number* but leaves little for the OS + capture + agent core on
a 32 GB box and is CPU-bound without a dGPU — keep it as a **fleet/cloud escalation via
LiteLLM** (§6 of the architecture), not the resident model.

### 1c. OCR accuracy on chip markings — what the benchmarks do and don't tell you

There is **no public chip-marking-OCR benchmark** [verify-at-adoption]. The closest proxies
are **OCRBench** (max 1000), **TextVQA**, **DocVQA**. Indicative OCRBench (model knowledge,
re-verify): MiniCPM-V 2.6 ≈ 850, Qwen2-VL-7B ≈ 845-866, Qwen2.5-VL-7B ≈ 860s,
Llama-3.2-Vision-11B markedly lower, moondown/LLaVA lower still.

**Caveat that dominates the design:** chip tops are the *adversarial* OCR case — low-contrast
laser etch, curved/reflective packages, tiny multi-line codes, vendor logos, date/lot codes
that look like part numbers. Document-OCR scores **overstate** real chip accuracy. Mitigations
(already in the wq#3900 design, confirmed correct here):

- **Preprocess** (wq#3900 §3): rectify, CLAHE, exposure-fuse a burst, tile so one IC's
  marking fills a meaningful fraction of the crop.
- **Two tracks cross-checked** (wq#3900 §4): VLM (semantic read + package/type guess) **and**
  a classical OCR engine — **PaddleOCR** primary / Tesseract fallback [verify-at-adoption] —
  for verbatim character strings; disagreement lowers confidence, never silently picks one.
- Treat the VLM read as a **candidate to cross-reference** against Part-DB/FlashDetector, not
  ground truth.

### 1d. Latency — 1080p image on i5-14500T, iGPU-only  [verify-at-adoption on hardware]

The cost driver is the **vision encoder + image-token prefill**, not text generation. A full
1080p frame into a dynamic-resolution model (Qwen2.5-VL) can expand to thousands of image
tokens; on a **CPU/iGPU-only** box that prefill is the bottleneck. Order-of-magnitude
expectations (must be measured on the real unit):

- `moondream`: low single-digit seconds/image.
- `qwen2.5-vl:7b` / `minicpm-v`, full 1080p, CPU-dominant: **~15-60 s/image** first-result.
- Same models **downscaled to ≤1280 px long edge and/or tiled**: materially faster and the
  recommended path — raw 1080p is wasteful for a single IC crop.
- **iGPU acceleration** (IPEX-LLM / Vulkan backend) can cut CPU numbers substantially but is
  not guaranteed in mainline Ollama for these archs — **verify on hardware** before promising
  interactive latency.

**Implication:** component ID is a **non-interactive, snapshot** task (seconds-to-minute is
fine). This rules the pipeline toward smaller models + aggressive downscale/tile, and makes
"live video" a non-goal for USE CASE 1.

### 1e. Structured JSON output — yes, enforce it, don't just ask

Ollama supports **schema-constrained output**: pass a JSON Schema in the `format` field
(Ollama ≥ 0.5 structured outputs) so generation is grammar-constrained, not merely prompted
[verify-at-adoption on installed version]. Qwen2.5-VL and MiniCPM-V both follow JSON
instructions reliably at 7-8B. Target per-component object (aligns with wq#3900 §4):

```json
{"bbox":[x,y,w,h],"package":"SOIC-8","pin_count":8,
 "markings":["W25Q128","JVSIQ"],"mfr_logo":"winbond|null",
 "type_guess":"flash|mcu|pmic|passive|connector|debug_header|unknown",
 "probable_function":"SPI NOR flash 128Mbit","confidence":0.0}
```

Enforce with `format`=schema + low temperature; keep `markings[]` verbatim (no
"correction") so the Part-DB lookup, not the model, resolves the canonical part.

---

## USE CASE 2 — HDMI capture for target-device video

### 2a. Magewell USB Capture HDMI Gen 2 — the validated gold path  [verify-at-adoption]

- **UVC/UAC-class compliant → zero proprietary driver on Linux.** Enumerates on the stock
  `uvcvideo` kernel module as a standard `/dev/video*` V4L2 device (+ `/dev/snd` for embedded
  audio). This is exactly the HAL's assumed path (`design/wq3806_limpet_architecture.md`
  line 194: "Capture/visual (Magewell HDMI) via uvcvideo").
- **USB VID `0x2935` (Magewell)** [verify-at-adoption — confirm PID of the specific SKU].
- Input/capture up to **1080p60**; output pixel formats incl. **YUYV** and **NV12** at full
  rate plus **MJPEG** (no USB-bandwidth downscale penalty like USB-2 dongles).
- Behaviour is stable and well-documented by the vendor for Linux — the reference device the
  HAL is designed around.

### 2b. Generic cheap UVC HDMI dongles — do they work?  [verify-at-adoption]

Yes, because they advertise the **USB Video Class**, so `uvcvideo` binds them with no driver.
The catch is bandwidth/format quirks, not driver support:

| Chipset | Bus | Real capability | Gotchas |
|---|---|---|---|
| **MacroSilicon MS2109** | USB 2.0 | input to 1080p; **YUYV only ~480p**, **MJPEG up to 1080p30** | the ubiquitous ~$10 dongle; must request `-input_format mjpeg` for HD; runs hot; lies about formats; VID:PID ≈ `534d:2109` |
| **MacroSilicon MS2130** | USB 3.0 | 1080p120 / 4K30-in (downscaled), uncompressed HD | newer "USB3.0 capture"; the cheap one worth buying |
| MS2131 / clones | USB 3.0 | similar to 2130 | label roulette — verify chipset, not box claims |

Rule of thumb: **USB-2 (MS2109) = MJPEG + 1fps snapshots fine**; **USB-3 (MS2130) if you
ever want uncompressed or higher cadence**. All bind `uvcvideo`; none need a vendor driver.

### 2c. Frame-capture rate needed for agent interaction

The agent reads **state**, it does not play a game: boot screens, menus, error dialogs,
progress. **1 fps on-demand snapshot is sufficient** and strongly preferred:

- A VLM read costs seconds (§1d) — a live stream would queue frames faster than they can be
  consumed. Pointless.
- Pattern: **event-driven single-frame** ("read_screen now"), optionally a slow poll
  (0.2-1 fps) with **frame-diff gating** so an unchanged screen triggers no new inference.
- Reserve higher cadence only for a transient-capture case (catch a fast boot banner): grab a
  short burst to disk with ffmpeg, then VLM only the frames that differ.

### 2d. Feeding frames to the VLM efficiently

- Capture one frame to memory/disk, **downscale to ≤1280 px long edge**, JPEG q≈85, base64
  into Ollama's `images` field (same core as USE CASE 1 — one vision path, two sources, per
  `design/wq3806_limpet_architecture.md` line 196).
- **Frame-diff gate** before inference (skip identical screens).
- For dense text (error codes, boot logs) raise resolution / lower JPEG compression for that
  call; for "what screen is this" keep it small.
- Screen text is high-contrast on flat UI → a **smaller/faster model (moondream or
  qwen2.5-vl:3b) is often enough for read_screen**, reserving the 7B for the harder PCB task.

---

## GENERAL

### G-a. UVC webcam compatibility on Ubuntu 26.04  [verify-at-adoption]

Effectively **every modern USB webcam is UVC-class and "just works"** on the stock
`uvcvideo` module (kernel 6.x on 26.04 is mature). Known-good references: Logitech C920 /
C922 / C930e / Brio. Only genuinely non-UVC/vendor-protocol cameras need special drivers
(rare, avoid). For PCB work, prefer a webcam with **manual focus + decent macro** over raw
megapixels — autofocus hunts on flat boards and resolution past ~1080p rarely helps the OCR.

### G-b. HAL device detection (udev / VID:PID / UVC class)

Grounded in the HAL contract: udev-driven inventory maps USB devices to hardware plugins by
**VID/PID + device path + driver** and exposes `list_hardware`
(`design/wq3806_limpet_architecture.md` lines 184-187). Detection rules for cameras/capture:

1. **Match the subsystem:** `SUBSYSTEM=="video4linux"`, driver `uvcvideo` → a V4L2 capture
   candidate.
2. **Class check:** USB interface class `0x0E` (Video) confirms UVC.
3. **Classify capture-card vs webcam** (both are UVC video — class alone can't tell them
   apart), in priority order:
   - **VID/PID allowlist** of known capture silicon → `role=hdmi_capture`:
     Magewell `0x2935`, MacroSilicon `0x534d` (MS2109/2130) [verify-at-adoption].
   - **Name-string** heuristic on the V4L2 card name: contains `HDMI` / `USB Capture` /
     `MACROSILICON` → `hdmi_capture`; else `webcam`.
   - **Capability probe**: a capture card reports **no HDMI input signal** (black/00 frames)
     when nothing is plugged into its HDMI port — a positive "signal present" distinguishes a
     live target from a disconnected card; a webcam always has signal.
4. **Emit the HAL descriptor** (`[hardware] VID/PID + path + driver`, `[capabilities]`,
   `[agent_use]`) per the §4 plugin shape, exposing `capture_frame` / `read_screen`.

### G-c. ffmpeg / V4L2 capture commands (still-frame extraction)

Enumerate and probe:

```bash
v4l2-ctl --list-devices                         # map cards → /dev/videoN
v4l2-ctl -d /dev/video0 --list-formats-ext      # real formats/resolutions (not box claims)
```

Single still frame (UVC webcam or Magewell, uncompressed path):

```bash
ffmpeg -f v4l2 -video_size 1920x1080 -i /dev/video0 -frames:v 1 -update 1 -y frame.jpg
```

MS2109 / USB-2 dongle (MUST request MJPEG for HD; drop the first frame, it is often garbage):

```bash
ffmpeg -f v4l2 -input_format mjpeg -video_size 1920x1080 -i /dev/video0 \
       -frames:v 2 -update 1 -y screen.jpg
```

Short burst to catch a transient boot banner, then diff/select offline:

```bash
ffmpeg -f v4l2 -input_format mjpeg -video_size 1920x1080 -i /dev/video0 \
       -t 3 -r 5 -y boot_%03d.jpg
```

(`fswebcam` is an acceptable lighter alternative for the webcam-only path.)

---

## OUTPUT — recommendations

**Vision model (resident on the box):** **`qwen2.5-vl:7b`** as the primary core for both use
cases — best-in-size OCR, native bounding boxes, reliable schema-constrained JSON; ~6 GB q4,
well inside the 14-32 GB budget. **`minicpm-v` (2.6)** as the cross-check / alternate on the
hard PCB reads. **`qwen2.5-vl:3b` or `moondream`** as the fast path for `read_screen`.
Escalate truly hard reads to `qwen2.5-vl:32b` or a cloud VLM via LiteLLM (§6), never resident.
Pair the VLM with classical **PaddleOCR** (Track B) for verbatim chip strings. Expect
**~15-60 s/full-1080p image CPU-bound, much less when downscaled/tiled — measure on the unit**;
treat both use cases as **snapshot, not streaming**.

**Validated HDMI capture compatibility (all `uvcvideo`, no driver):**
- **Recommended:** Magewell USB Capture HDMI Gen 2 (VID `0x2935`), 1080p60, YUYV/NV12/MJPEG.
- **Budget-good:** MacroSilicon **MS2130** USB-3 dongle, 1080p uncompressed.
- **Budget-minimum:** MacroSilicon **MS2109** USB-2 dongle — HD only via MJPEG, fine for 1 fps
  snapshots.
- **Webcams (PCB):** any UVC cam; Logitech C920/C922/Brio class, manual-focus preferred.

**V4L2 capture pipeline:** `v4l2-ctl --list-devices` / `--list-formats-ext` to probe → `ffmpeg
-f v4l2 … -frames:v 1 -update 1` for uncompressed, `-input_format mjpeg` (+drop first frame)
for USB-2 dongles → downscale ≤1280 px, JPEG q≈85, base64 into Ollama `images`, frame-diff
gate before each inference.

**HAL detection rules:** `SUBSYSTEM=="video4linux"` + `uvcvideo` + USB class `0x0E`; classify
`hdmi_capture` vs `webcam` by VID/PID allowlist (Magewell `0x2935`, MacroSilicon `0x534d`) →
card-name string → HDMI-signal-present probe; emit the §4 HAL descriptor exposing
`capture_frame` / `read_screen`.

**Unblocks:** Target Device Wizard vision path (`docs/target-wizard-spec.md`) and the capture
+ read stages of `design/wq3900_limpet_vision_component_id.md`.
