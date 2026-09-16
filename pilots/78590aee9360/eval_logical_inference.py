#!/usr/bin/env python3
"""eval_logical_inference.py — pilot 78590aee9360 (wq#1903).

Minimal version of the proposal's eval_logical_inference.py: runs a fixed
set of True/False propositional-logic questions (sample.json, built by
build_sample.py) against a configurable list of LiteLLM-routed models and
writes a pass-rate report.

Key resolution copies prompt-forge/llm.py's env-first, key-file-fallback
pattern (docs/litellm/service_keys.md convention) rather than inventing a
new one. Stdlib-only, same as prompt-forge/llm.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

GATEWAY = os.environ.get("PROMPT_FORGE_LITELLM_URL", "http://127.0.0.1:4000")
KEY_FILE = Path(
    os.environ.get(
        "PROMPT_FORGE_KEY_FILE",
        "/home/lumen/.secrets/litellm-service-keys/prompt-forge.key",
    )
)
TIMEOUT_S = 60


def _api_key() -> str | None:
    key = os.environ.get("LITELLM_API_KEY", "").strip()
    if key:
        return key
    try:
        key = KEY_FILE.read_text().strip()
    except OSError:
        return None
    return key or None


def _chat(model: str, prompt: str, key: str) -> str | None:
    body = json.dumps({
        "model": model,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": "Answer with exactly one word: True or False."},
            {"role": "user", "content": prompt},
        ],
    }).encode()
    req = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError):
        return None


def _parse_verdict(reply: str | None) -> str | None:
    if not reply:
        return None
    lowered = reply.strip().lower()
    if lowered.startswith("true"):
        return "True"
    if lowered.startswith("false"):
        return "False"
    if "true" in lowered and "false" not in lowered:
        return "True"
    if "false" in lowered and "true" not in lowered:
        return "False"
    return None


def run(models: list[str], sample_path: Path) -> dict:
    key = _api_key()
    if not key:
        return {"error": "no LiteLLM key available", "models": {}}

    items = json.loads(sample_path.read_text())["items"]
    results: dict[str, dict] = {}
    for model in models:
        per_item = []
        correct = 0
        answered = 0
        t0 = time.monotonic()
        for item in items:
            reply = _chat(model, item["prompt"], key)
            verdict = _parse_verdict(reply)
            hit = verdict == item["answer"]
            if verdict is not None:
                answered += 1
            if hit:
                correct += 1
            per_item.append({
                "id": item["id"],
                "pattern": item["pattern"],
                "expected": item["answer"],
                "got": verdict,
                "raw": reply,
                "correct": hit,
            })
        elapsed = time.monotonic() - t0
        results[model] = {
            "pass_rate": correct / len(items),
            "answered": answered,
            "total": len(items),
            "elapsed_s": round(elapsed, 1),
            "items": per_item,
        }
    return {"models": results}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--sample", type=Path, default=Path(__file__).parent / "sample.json")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "results.json")
    args = ap.parse_args()

    report = run(args.models, args.sample)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    if "error" in report:
        print(report["error"], file=sys.stderr)
        return 1
    for model, r in report["models"].items():
        print(f"{model}: pass_rate={r['pass_rate']:.2f} ({r['answered']}/{r['total']} parsed) {r['elapsed_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
