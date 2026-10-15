"""wq#3943 — component manifest assembly for the LIMPET vision pipeline.

Ties capture_ops (stage 1), vision_ops (stage 3 read), and
component_lookup_ops (stage 4 resolve) into the stage-5 deliverable:
`components.json` written to `<workspace>/evidence/`, matching the schema in
design/wq3900_limpet_vision_component_id.md §6. File input works with zero
camera attached (`frames` can be pre-existing image paths) — the camera path
through capture_ops is a convenience, not a dependency, per that design's §2.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import capture_ops
import component_lookup_ops
import vision_ops

SCHEMA = "limpet.component-manifest/1"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def run_pipeline(
    workspace: str,
    frames: list[str] | None = None,
    device: str = "",
    model: str = vision_ops.DEFAULT_MODEL,
) -> dict:
    """Run capture (if `frames` is empty and `device` is given) -> VLM read ->
    resolve -> manifest, and write the manifest to
    `<workspace>/evidence/components.json`. Returns the manifest dict.
    Raises ValueError if neither `frames` nor `device` is given, and
    RuntimeError if a requested capture fails outright (a missing/garbled
    read on an existing frame degrades into `unresolved` instead — only a
    capture that never produced a frame at all is fatal)."""
    ws = Path(workspace)
    frame_paths = [Path(f) for f in (frames or [])]

    if not frame_paths and device:
        capture_path = ws / "target" / "frames" / f"capture-{int(time.time())}.jpg"
        result = capture_ops.capture_frame(device, str(capture_path))
        if not result["ok"]:
            raise RuntimeError(f"capture_frame failed for device={device!r}: {result['stderr']}")
        frame_paths = [capture_path]

    if not frame_paths:
        raise ValueError("run_pipeline requires frames=[...] or device=<logical name>")

    components = []
    unresolved = []

    for frame in frame_paths:
        read = vision_ops.identify_components(str(frame), model=model)
        if not read["ok"]:
            unresolved.append({"frame": str(frame), "error": read["error"]})
            continue
        for i, comp in enumerate(read["components"]):
            markings = comp.get("markings") or []
            resolved = None
            for marking in markings:
                hit = component_lookup_ops.lookup_part(marking, comp.get("package", ""))
                if hit["resolved"]:
                    resolved = hit["resolved"]
                    break
            entry = {
                "id": f"{frame.stem}-{i}",
                "bbox": {"frame": str(frame), "xywh": comp.get("bbox")},
                "package": comp.get("package"),
                "markings": markings,
                "type": comp.get("type_guess"),
                "part": resolved,
                "confidence": comp.get("confidence"),
                "needs_review": resolved is None,
                "read_by": {"vlm": read["model"]},
                "re_notes": [comp["probable_function"]] if comp.get("probable_function") else [],
            }
            if resolved is None:
                unresolved.append({"id": entry["id"], "markings": markings})
            else:
                components.append(entry)

    manifest = {
        "schema": SCHEMA,
        "board": {
            "workspace": str(workspace),
            "frames": [{"path": str(f), "sha256": _sha256(f)} for f in frame_paths if f.is_file()],
        },
        "components": components,
        "unresolved": unresolved,
        "run": {
            "pipeline_version": "0.1.0",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "escalated_to_cloud": False,
        },
    }

    evidence_dir = ws / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "components.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def wizard_candidates(image_path: str, model: str = vision_ops.DEFAULT_MODEL) -> list[dict]:
    """Adapter for the Target Device Wizard's IDENTIFY-stage vision contract
    (docs/target-wizard-spec.md §4.2: in = image/frame handle + optional ROI;
    out = [{part_number, manufacturer, confidence, bbox}]). Reads one image,
    resolves each transcribed marking, and flattens to that shape — per
    wq3900 §7 the wizard integration is a contract only, so this adapter is
    the entire coupling: nothing else in this ops dir imports wizard code."""
    read = vision_ops.identify_components(image_path, model=model)
    if not read["ok"]:
        return []
    candidates = []
    for comp in read["components"]:
        for marking in comp.get("markings") or []:
            hit = component_lookup_ops.lookup_part(marking, comp.get("package", ""))
            resolved = hit["resolved"]
            if resolved:
                candidates.append({
                    "part_number": resolved.get("mpn") or marking,
                    "manufacturer": resolved.get("manufacturer", ""),
                    "confidence": comp.get("confidence", 0.5),
                    "bbox": comp.get("bbox"),
                })
    return candidates
