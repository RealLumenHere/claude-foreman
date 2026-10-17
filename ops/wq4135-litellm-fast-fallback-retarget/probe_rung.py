#!/usr/bin/env python3
"""wq#4135 — real (non-mock) completion probe for a LiteLLM model group.

probe_alias_fallback.py (wq#4102) proves a chain is *walked* by mocking a
primary failure; it cannot tell a live rung from a dead one. This sends an
ordinary 1-token completion so the named group's own deployment must answer.

  probe_rung.py cerebras-llama deepseek-v4-flash qwen36-local

Per model prints HTTP status, the answering group, and a masked error body on
non-200. Exit 0 if every model answered 200 *without* falling back
(x-litellm-attempted-fallbacks absent or 0), else 1. Auth is the completion
probe's service key (read from file, sent in the header, never printed).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/home/lumen/foreman/harness")
from masking import mask_text  # noqa: E402

URL = os.environ.get("LITELLM_URL", "http://127.0.0.1:4000") + "/v1/chat/completions"
KEY_PATH = os.environ.get("LITELLM_COMPLETION_PROBE_KEY_FILE",
                          "/home/lumen/.secrets/litellm-service-keys/model-watchdog.key")


def probe(model: str, key: str, timeout: int) -> dict:
    body = json.dumps({"model": model, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "ok"}]}).encode()
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
           "model_group": headers.get("x-litellm-model-group"),
           "attempted_fallbacks": headers.get("x-litellm-attempted-fallbacks")}
    if status != 200:
        text = mask_text(raw.decode(errors="replace"))[0]
        out["error"] = text if len(text) <= 400 else text[:200] + " ... " + text[-200:]
    return out


def answered_itself(r: dict) -> bool:
    return r["status"] == 200 and (r.get("attempted_fallbacks") or "0") in ("0", "None")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("models", nargs="+")
    ap.add_argument("--timeout", type=int, default=90)
    a = ap.parse_args(argv)
    with open(KEY_PATH) as f:
        key = f.read().strip()
    results = [probe(m, key, a.timeout) for m in a.models]
    for r in results:
        print(json.dumps({**r, "alive": answered_itself(r)}))
    return 0 if all(map(answered_itself, results)) else 1


if __name__ == "__main__":
    sys.exit(main())
