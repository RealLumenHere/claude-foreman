#!/usr/bin/env python3
"""wq#4102 — prove a model name falls back when its primary fails, without
stopping anything: each request carries `mock_testing_fallbacks: true`, which
makes the LiteLLM router raise on the primary deployment and walk the chain it
finds for that exact name.

  probe_alias_fallback.py lumen/best deepseek-v4-pro lumen/fast

Per model, prints HTTP status, the group that answered, and
x-litellm-attempted-fallbacks. Exit 1 if any model did not answer via a
fallback. Auth is the completion probe's service key (read from file, sent in
the header, never printed). Error bodies are masked before printing.
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
    body = json.dumps({"model": model, "max_tokens": 1, "mock_testing_fallbacks": True,
                       "messages": [{"role": "user", "content": "ok"}]}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, headers, raw = r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        status, headers, raw = e.code, e.headers, e.read()
    out = {"model": model, "status": status,
           "model_group": headers.get("x-litellm-model-group"),
           "attempted_fallbacks": headers.get("x-litellm-attempted-fallbacks")}
    if status != 200:
        text = mask_text(raw.decode(errors="replace"))[0]
        out["error"] = text if len(text) <= 300 else text[:120] + " ... " + text[-180:]
    return out


def fell_back(r: dict) -> bool:
    return r["status"] == 200 and (r["attempted_fallbacks"] or "0") not in ("0", "None")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("models", nargs="+")
    ap.add_argument("--timeout", type=int, default=90)
    a = ap.parse_args(argv)
    with open(KEY_PATH) as f:
        key = f.read().strip()
    results = [probe(m, key, a.timeout) for m in a.models]
    for r in results:
        print(json.dumps({**r, "fell_back": fell_back(r)}))
    return 0 if all(map(fell_back, results)) else 1


if __name__ == "__main__":
    sys.exit(main())
