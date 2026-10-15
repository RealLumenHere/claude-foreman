# wq#3943 — LIMPET vision pipeline: webcam/HDMI capture → component identification

Implements the pipeline designed in `design/wq3900_limpet_vision_component_id.md`
and sized/sourced in `research/WQ3922_VISION_HDMI_CAPTURE.md`: capture a still
frame → read it with a local multimodal Ollama model → resolve markings to a
part identity → write a structured `components.json` manifest. File input
works with zero camera attached — capture is a convenience, not a dependency
(design doc §2).

## Pipeline stages → modules

| Stage | Module | Notes |
|---|---|---|
| 1. Capture | `capture_ops.py` | ffmpeg/v4l2 still frame; resolves a HAL logical name ("webcam", "hdmi_capture") or a literal `/dev/videoN` |
| 2. Vision read | `vision_ops.py` | Ollama `/api/generate`, schema-constrained JSON, `qwen2.5-vl:7b` default per wq#3922 OUTPUT |
| 3. Resolve | `component_lookup_ops.py` | flash-marking decode (local, or the real FlashDetector CLI if installed) → Part-DB REST (optional) |
| 4/5. Manifest | `manifest_ops.py` | assembles `components.json` per design doc §6, writes to `<workspace>/evidence/` |
| MCP surface | `server.py` | `limpet_capture_frame`, `limpet_identify_components`, `limpet_component_lookup`, `limpet_component_manifest` |
| Health | `selftest.py` | the `[health].command` in `limpet/examples/component-id.toml` |

## What's real vs. degraded-by-design

- **Capture**: real ffmpeg invocation. No camera is attached to this build
  box, so it's untested against live hardware — `resolve_device`/`capture_frame`
  are unit-tested against a faked `subprocess.run`.
- **Vision read**: real Ollama HTTP call (stdlib `urllib`, no new dependency).
  `qwen2.5-vl:7b` is **not pulled** on this box (confirmed via `ollama list`,
  2026-10-15) — pulling a ~6GB VLM is its own TOOL_REGISTRY adoption
  (design doc §7), not something this ticket silently provisions. Until it's
  pulled, `limpet_identify_components` returns a legible
  `{"ok": false, "error": "ollama HTTP 404: ..."}` rather than crashing — the
  exact "pipeline must run degraded" posture the design calls for, extended
  to the resident model itself.
- **FlashDetector**: the real `iTXTech/FlashDetector` CLI is **not adopted**
  (no TOOL_REGISTRY row yet). `component_lookup_ops.flash_marking_decode`
  shells out to it if present (`shutil.which("flashdetector")`), else falls
  back to a conservative local decode over well-documented manufacturer
  marking prefixes (Winbond `25Q`, GigaDevice `GD25Q`, Macronix `MX25L`, …) —
  manufacturer+family only, never a guessed full part number.
- **Part-DB**: **not adopted/running** anywhere in the fleet yet.
  `component_lookup_ops.partdb_search` is a real REST client gated on
  `LIMPET_PARTDB_BASE_URL`; unset (the default), it returns
  `{"ok": false, "error": "...not configured..."}`. The exact resource path
  (`PARTDB_SEARCH_PATH`) is marked `[verify-at-adoption]` — confirm it against
  the real Part-DB-server instance's API-Platform routes before trusting a hit.
- **OCR cross-check (Track B)**: not built — design doc §9 build order puts it
  after the resolver chain lands; this ticket is build-order steps 1-2 plus
  the HAL/manifest/MCP/wizard-contract pieces.

No resolver or the vision stage ever upgrades a miss to a guessed identity
(§4's hallucination control): `lookup_part` returns `resolved: None` and
`needs_review: true` rather than fabricating a part, and `selftest.py` asserts
exactly that for an unresolvable marking.

## HAL integration (ticket item 6)

`ops/wq3809-limpet-hal/hal.py` already matched Magewell HDMI capture by
VID:PID; this ticket adds:
- MS2109/MS2130 generic capture-dongle VID:PID pairs to that same plugin
  entry (`limpet/hardware.d/06-hdmi-capture.toml` already catalogued them,
  confidence "low" — rebrand/batch-dependent, so this is a weak signal).
- `hal.detect_video_devices()` — a sysfs walk over
  `/sys/class/video4linux/video*` classifying every `uvcvideo`-bound node as
  `hdmi_capture` or `webcam` by card-name heuristic
  (`research/WQ3922_VISION_HDMI_CAPTURE.md` §G-b), the only way to find a
  generic UVC webcam at all — it has no stable VID:PID by design
  (`limpet/hardware.d/07-usb-webcams.toml`). Exposed as `hal.py detect-video`.
  Kept as a separate function (not merged into `detect()`'s REGISTRY loop) so
  that function's existing VID:PID contract and test suite are untouched.
- `capture_ops.list_capture_devices()` combines both (`detect` + `detect-video`,
  deduped by device node) — the device-resolution path `capture_frame` and
  `limpet_capture_frame` actually use.

## Target Device Wizard integration (ticket's "Step 2")

`docs/target-wizard-spec.md` §4.2 specifies the wizard's vision contract as
exactly: in = image/frame handle + optional ROI; out =
`[{part_number, manufacturer, confidence, bbox}]`. `manifest_ops.wizard_candidates`
is that adapter — the whole coupling, per wq#3900 §7 ("contract only, no
coupling to that design's internals"). Nothing else in this ops dir imports
wizard code; the wizard itself is tracked separately (wq#3901, not yet built).

## Registering as a meta-server child

```json
{
  "name": "limpet-vision",
  "transport": "stdio",
  "command": ["python3", "/path/to/ops/wq3943-limpet-vision-pipeline-a1/server.py"],
  "enabled": true
}
```

## Config (env vars, all optional — the pipeline degrades cleanly without any of them)

| Var | Default | Purpose |
|---|---|---|
| `LIMPET_VISION_MODEL` | `qwen2.5-vl:7b` | Ollama model tag |
| `LIMPET_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama server |
| `LIMPET_PARTDB_BASE_URL` | `""` (disabled) | Part-DB REST base URL |
| `LIMPET_PARTDB_API_TOKEN` | `""` | Part-DB bearer token |
| `LIMPET_FFMPEG_BIN` | `which ffmpeg` | ffmpeg binary override |
| `LIMPET_HAL_SCRIPT` | `/home/lumen/foreman/ops/wq3809-limpet-hal/hal.py` | HAL script path |

## Tests

```sh
python3 -m pytest ops/wq3943-limpet-vision-pipeline-a1/tests -q
python3 -m pytest ops/wq3809-limpet-hal/tests -q   # detect_video_devices coverage
```

No real ffmpeg, Ollama, FlashDetector, Part-DB, or hardware is exercised —
`subprocess.run` / `urllib.request.urlopen` / `shutil.which` are monkeypatched
throughout, same pattern as `ops/wq3882-limpet-flashrom-mcp-a1`.
