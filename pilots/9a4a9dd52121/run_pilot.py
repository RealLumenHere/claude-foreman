#!/usr/bin/env python3
"""wq#1570 pilot runner — DeLS-Spec-style draft+verify vs direct target decoding.

Auth follows /home/lumen/foreman/prompt-forge/llm.py (env LITELLM_API_KEY, else
the prompt-forge service-key file). The key is read by this process and never
printed; only latencies and model text are logged.

Substitution note (same precedent as wq#1560, foreman/kb ISSUE-LOG #257): the
ticket carries `needs:` empty, so fworker.resolve_exec_grant wrote no
FOREMAN_GRANT_FILE and every `fleet-exec run hathor` denies with
grant:grant_missing_env (confirmed live this run: `fleet-exec grant` ->
"DENY [grant:grant_missing_env]: grant file did not verify"). There is also no
fleet-exec verb to execute an arbitrary command on hathor even with a grant
(policy allows only run_readonly_cmds + copy-into-pilots for hathor, no general
exec). Both arms therefore run from Lumen against the LiteLLM gateway
(127.0.0.1:4000), which itself proxies hathor-lfm-1b onto hathor for the draft
call -- so the CPU-only hathor hardware is still exercised for the draft half
of the treatment, only the harness process runs on Lumen instead of hathor.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "/home/lumen/foreman/prompt-forge")
import llm  # noqa: E402

GATEWAY = "http://127.0.0.1:4000"
DRAFT_MODEL = "hathor-lfm-1b"     # LFM small model served from hathor (CPU-only node)
TARGET_MODEL = "gemma4-e4b-local"  # local gemma4 target model
TIMEOUT_S = 90

SAMPLE = [
    "In one sentence, what is the capital of France?",
    "In one sentence, what does the Python `len()` builtin do?",
    "In one sentence, what is the boiling point of water at sea level in Celsius?",
    "In one sentence, what does HTTP stand for?",
    "In one sentence, what is the chemical symbol for gold?",
    "In one sentence, what is the primary purpose of a git commit?",
]


def call(model: str, prompt: str, max_tokens: int) -> tuple[str | None, float]:
    key = llm._api_key()
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError) as e:
        return None, time.monotonic() - t0
    return text, time.monotonic() - t0


def main() -> None:
    if not llm.available():
        print(json.dumps({"error": "no LITELLM_API_KEY / key file available"}))
        sys.exit(1)

    results = []
    for i, prompt in enumerate(SAMPLE):
        # baseline: target model direct
        base_text, base_lat = call(TARGET_MODEL, prompt, 100)

        # treatment: draft model, then target model verifies/corrects the draft
        draft_text, draft_lat = call(DRAFT_MODEL, prompt, 100)
        verify_prompt = (
            f"Question: {prompt}\n"
            f"Candidate answer: {draft_text!r}\n"
            "If the candidate answer is correct and one sentence, reply with exactly "
            "that sentence. Otherwise reply with the corrected one-sentence answer."
        )
        verify_text, verify_lat = call(TARGET_MODEL, verify_prompt, 100)
        treat_lat = draft_lat + verify_lat

        row = {
            "id": i,
            "prompt": prompt,
            "baseline": {"model": TARGET_MODEL, "text": base_text, "latency_s": round(base_lat, 3)},
            "treatment": {
                "draft_model": DRAFT_MODEL,
                "draft_text": draft_text,
                "draft_latency_s": round(draft_lat, 3),
                "verify_model": TARGET_MODEL,
                "verify_text": verify_text,
                "verify_latency_s": round(verify_lat, 3),
                "total_latency_s": round(treat_lat, 3),
            },
        }
        results.append(row)
        print(f"[{i}] baseline={base_lat:.2f}s treatment={treat_lat:.2f}s "
              f"(draft={draft_lat:.2f}s verify={verify_lat:.2f}s)", flush=True)

    out = Path(__file__).parent / "results.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
