#!/usr/bin/env python3
"""wq#4423 — real (non-mock) vision completion probe for `lumen/vision`.

Like wq#4135's probe_rung.py, but sends an actual image (base64 data URL)
in a multimodal chat message instead of a 1-token text probe — a vision
deployment can return 200 on a text-only prompt while being unable to see
anything, so this is the only probe that proves the swap to qwen3-vl:8b
actually answers questions about a real image (e.g. a PCB photo from the
Miko dumps, per the ticket's acceptance bar).

  probe_vision.py --image /path/to/pcb.jpg [--model lumen/vision]
     [--prompt "what components do you see?"]

Prints HTTP status, the answering model group, and the (masked) response
text or error body. Exit 0 on a 200 that mentions something other than a
refusal/empty answer, else 1. Auth is the completion probe's service key
(read from file, sent in the header, never printed).
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/home/lumen/foreman/harness")
from masking import mask_text  # noqa: E402

URL = os.environ.get("LITELLM_URL", "http://127.0.0.1:4000") + "/v1/chat/completions"
KEY_PATH = os.environ.get("LITELLM_COMPLETION_PROBE_KEY_FILE",
                          "/home/lumen/.secrets/litellm-service-keys/model-watchdog.key")
MODEL = "lumen/vision"
PROMPT = "Describe what you see in this image in one sentence."


def _data_url(image_path: str) -> str:
    mime, _ = mimetypes.guess_type(image_path)
    mime = mime or "image/jpeg"
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def probe(image_path: str, model: str, prompt: str, key: str, timeout: int) -> dict:
    body = json.dumps({
        "model": model,
        "max_tokens": 200,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _data_url(image_path)}},
            ],
        }],
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, headers, raw = r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        status, headers, raw = e.code, e.headers, e.read()
    except OSError as e:
        return {"model": model, "status": None, "error": mask_text(str(e))[0]}
    out = {"model": model, "status": status,
           "model_group": headers.get("x-litellm-model-group")}
    text = raw.decode(errors="replace")
    if status == 200:
        try:
            out["answer"] = json.loads(text)["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError):
            out["answer"] = mask_text(text)[0][:400]
    else:
        masked = mask_text(text)[0]
        out["error"] = masked if len(masked) <= 400 else masked[:200] + " ... " + masked[-200:]
    return out


def answered(r: dict) -> bool:
    return r["status"] == 200 and bool((r.get("answer") or "").strip())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--prompt", default=PROMPT)
    ap.add_argument("--timeout", type=int, default=90)
    a = ap.parse_args(argv)
    with open(KEY_PATH) as f:
        key = f.read().strip()
    r = probe(a.image, a.model, a.prompt, key, a.timeout)
    print(json.dumps({**r, "answered": answered(r)}))
    return 0 if answered(r) else 1


if __name__ == "__main__":
    sys.exit(main())
