"""wq#3943 — vision-model component reads for the LIMPET vision pipeline.

Sends one preprocessed crop/frame to a local multimodal Ollama model and
returns per-component {bbox, package, markings[], mfr_logo, type_guess,
probable_function, confidence} per
design/wq3900_limpet_vision_component_id.md §4 and
research/WQ3922_VISION_HDMI_CAPTURE.md §1e. Schema-constrained (Ollama's
`format`) rather than merely prompted — a 7-8B VLM follows JSON instructions
reliably at this size, but a free-text response still drifts under load.

Uses urllib (stdlib) rather than `requests`/the `ollama` package so this stays
importable without a pip install (same reason flashrom_ops.py avoids a new
dependency); scripts/ollama_toolprobe.py already uses the same raw
/api/generate|/api/chat shape fleet-wide.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

# research/WQ3922_VISION_HDMI_CAPTURE.md OUTPUT: qwen2.5-vl:7b is the recommended
# resident core for both PCB and screen-read use cases (best-in-size OCR, native
# bboxes, reliable schema JSON at ~6GB q4). Override per-call or via env for the
# cheaper read_screen path (qwen2.5-vl:3b/moondream) or a cloud escalation.
DEFAULT_MODEL = os.environ.get("LIMPET_VISION_MODEL", "qwen2.5-vl:7b")
DEFAULT_BASE_URL = os.environ.get("LIMPET_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_TIMEOUT_SEC = 120

# design/wq3900_limpet_vision_component_id.md §4 hallucination control: verbatim
# transcription, explicit "?" for an unreadable glyph, never complete a part number
# from training knowledge. Resolution happens downstream in component_lookup_ops
# against FlashDetector/Part-DB, not here.
PROMPT = (
    "You are reading a macro photo of one or more electronic components on a PCB. "
    "For every component visible, transcribe its top marking EXACTLY as printed -- "
    "every line, verbatim, using '?' for any glyph you cannot read with confidence. "
    "Never complete, correct, or guess a full part number from what the marking might "
    "mean; report only what is physically printed. For each component give: bbox "
    "[x,y,w,h] in pixels, package (e.g. SOIC-8, QFP-64, 0603), pin_count if countable "
    "else null, markings as a list of verbatim lines, mfr_logo if a manufacturer logo "
    "is recognizable else null, type_guess (one of: flash, mcu, pmic, passive, "
    "connector, debug_header, unknown), probable_function (a short phrase), and "
    "confidence (0.0-1.0, your own transcription confidence, not identification "
    "confidence)."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["components"],
    "properties": {
        "components": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["markings", "confidence"],
                "properties": {
                    "bbox": {"type": "array", "items": {"type": "number"}},
                    "package": {"type": "string"},
                    "pin_count": {"type": ["integer", "null"]},
                    "markings": {"type": "array", "items": {"type": "string"}},
                    "mfr_logo": {"type": ["string", "null"]},
                    "type_guess": {"type": "string"},
                    "probable_function": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        }
    },
}


class VisionError(RuntimeError):
    pass


def identify_components(
    image_path: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """Read `image_path` with the local VLM and return
    {"ok", "model", "components": [...], "error"}. Never raises on a model or
    connection failure (Ollama not running, model not pulled) — that is a
    legitimate degraded pipeline result
    (design/wq3900_limpet_vision_component_id.md §7: the pipeline must run
    degraded when an external dependency isn't provisioned yet), so the
    caller gets a legible `error` string instead of a crash."""
    try:
        with open(image_path, "rb") as fh:
            image_bytes = fh.read()
    except OSError as exc:
        return {"ok": False, "model": model, "components": [], "error": f"cannot read {image_path}: {exc}"}

    body = {
        "model": model,
        "prompt": PROMPT,
        "images": [base64.b64encode(image_bytes).decode("ascii")],
        "format": RESPONSE_SCHEMA,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/generate",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        return {"ok": False, "model": model, "components": [], "error": f"ollama HTTP {exc.code}: {detail}"}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "model": model, "components": [], "error": f"ollama unreachable at {base_url}: {exc}"}

    raw = payload.get("response", "")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {
            "ok": False, "model": model, "components": [],
            "error": f"model did not return valid JSON: {exc}", "raw": raw[:500],
        }

    components = parsed.get("components")
    if not isinstance(components, list):
        return {
            "ok": False, "model": model, "components": [],
            "error": "model response missing a 'components' array", "raw": raw[:500],
        }

    return {"ok": True, "model": model, "components": components, "error": None}
