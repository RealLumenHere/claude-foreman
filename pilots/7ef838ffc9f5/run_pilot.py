#!/usr/bin/env python3
"""wq#1902 pilot runner — seed a personalization eval harness for Lumen.

Baseline arm: query sent with no persona/memory context (today's default —
lumen-persona-engine / lumen-convo-learn context not modeled, since neither
service's code lives in this worktree; this pilot tests the harness/metric,
not a live integration, per the card's "substantial project, defer" reason).

Treatment arm: the same query, with the task's persona/preference facts
injected as a system-prompt context block — the smallest stand-in for what
lumen-persona-engine's lp_*.txt prompts + lumen-convo-learn's extracted
memory would supply to the model.

Auth follows /home/lumen/foreman/prompt-forge/llm.py (env LITELLM_API_KEY,
else the prompt-forge service-key file). The key is read by this process and
never printed; only latencies, scores, and model text are logged.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "/home/lumen/foreman/prompt-forge")
import llm  # noqa: E402

GATEWAY = "http://127.0.0.1:4000"
TARGET_MODEL = "gemma4-e4b-local"  # llm.chat's own default (claude-sonnet-5) returned
                                    # None against this gateway/key; call the model
                                    # explicitly instead, same pattern as pilot 9a4a9dd52121
NO_CONTEXT_SYSTEM = "You are a helpful assistant."


def call(system: str, user: str, max_tokens: int) -> str | None:
    key = llm._api_key()
    body = json.dumps({
        "model": TARGET_MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode()
    req = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError):
        return None


def score(task: dict, text: str | None) -> float:
    if not text:
        return 0.0
    lower = text.lower()
    hits = 0
    total = 0
    for word in task.get("rubric", []):
        total += 1
        if word.lower() in lower:
            hits += 1
    if task.get("forbidden"):
        total += 1
        if not any(w.lower() in lower for w in task["forbidden"]):
            hits += 1
    if "max_sentences" in task:
        total += 1
        n_sentences = len([s for s in re.split(r"[.!?]", text) if s.strip()])
        if n_sentences <= task["max_sentences"]:
            hits += 1
    if total == 0:
        return 1.0
    return hits / total


def run(arm: str, sample: list[dict]) -> list[dict]:
    rows = []
    for task in sample:
        system = NO_CONTEXT_SYSTEM if arm == "baseline" else (
            f"You are a helpful assistant. Known facts about this user: {task['facts']}"
        )
        t0 = time.monotonic()
        text = call(system, task["query"], max_tokens=200)
        lat = time.monotonic() - t0
        s = score(task, text)
        rows.append({
            "id": task["id"],
            "arm": arm,
            "query": task["query"],
            "response": text,
            "score": s,
            "latency_s": round(lat, 3),
        })
        print(f"[{arm}][{task['id']}] score={s:.2f} lat={lat:.2f}s", flush=True)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["baseline", "treatment"])
    ap.add_argument("--sample", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if not llm.available():
        print(json.dumps({"error": "no LITELLM_API_KEY / key file available"}))
        sys.exit(1)

    sample = json.loads(Path(args.sample).read_text())["tasks"]
    rows = run(args.arm, sample)
    Path(args.out).write_text(json.dumps(rows, indent=2))
    mean_score = sum(r["score"] for r in rows) / len(rows)
    print(f"wrote {args.out}  mean_score={mean_score:.3f}")


if __name__ == "__main__":
    main()
