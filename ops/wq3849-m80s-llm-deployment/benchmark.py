#!/usr/bin/env python3
"""wq#3849 -- benchmark each pulled model on M80s at 16K and 32K context.

Run locally on M80s (or wherever Ollama's API is reachable at OLLAMA_HOST) once
pull-models.sh has completed. Uses only the stdlib so it needs nothing installed
beyond Python 3 and a running `ollama serve`.

Measures prompt-eval (prefill) and generation (decode) tok/s from Ollama's own
/api/generate response fields (prompt_eval_count/duration, eval_count/duration,
all durations in nanoseconds) -- the numbers the server itself timed, not a
wall-clock wrapper around it.
"""
import json
import os
import sys
import time
import urllib.request

HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

MODELS = [
    "qwen3.6:35b-a3b",
    "qwen2.5-coder:32b",
    "qwen3:32b",
    "qwen2.5-coder:14b",
]
CONTEXTS = [16384, 32768]

# Repeated filler so the prompt itself approaches num_ctx; leaves ~2K tokens of
# headroom for the model's own response within the context window.
FILLER = (
    "The quick brown fox jumps over the lazy dog near the riverbank at dawn. "
)


def build_prompt(num_ctx: int) -> str:
    target_tokens = max(num_ctx - 2048, 512)
    # ~4 chars/token is a reasonable English-text estimate for sizing the filler;
    # the actual tokenizer decides the real count, this just gets us close.
    target_chars = target_tokens * 4
    reps = max(target_chars // len(FILLER), 1)
    body = FILLER * reps
    return body + "\n\nSummarize the repeated sentence above in one sentence."


def call_generate(model: str, prompt: str, num_ctx: int) -> dict:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": num_ctx},
    }
    req = urllib.request.Request(
        f"{HOST}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=1800) as resp:
        return json.loads(resp.read())


def run() -> list[dict]:
    results = []
    for model in MODELS:
        for num_ctx in CONTEXTS:
            prompt = build_prompt(num_ctx)
            t0 = time.monotonic()
            try:
                resp = call_generate(model, prompt, num_ctx)
            except Exception as exc:  # noqa: BLE001 -- record and continue
                results.append(
                    {"model": model, "num_ctx": num_ctx, "error": str(exc)}
                )
                continue
            wall_s = time.monotonic() - t0

            prompt_eval_count = resp.get("prompt_eval_count", 0)
            prompt_eval_duration_ns = resp.get("prompt_eval_duration", 0)
            eval_count = resp.get("eval_count", 0)
            eval_duration_ns = resp.get("eval_duration", 0)

            prefill_tok_s = (
                prompt_eval_count / (prompt_eval_duration_ns / 1e9)
                if prompt_eval_duration_ns
                else None
            )
            decode_tok_s = (
                eval_count / (eval_duration_ns / 1e9) if eval_duration_ns else None
            )

            row = {
                "model": model,
                "num_ctx": num_ctx,
                "wall_s": round(wall_s, 2),
                "prompt_eval_count": prompt_eval_count,
                "prefill_tok_s": round(prefill_tok_s, 2) if prefill_tok_s else None,
                "eval_count": eval_count,
                "decode_tok_s": round(decode_tok_s, 2) if decode_tok_s else None,
            }
            results.append(row)
            print(json.dumps(row))
    return results


if __name__ == "__main__":
    out_path = sys.argv[1] if len(sys.argv) > 1 else "benchmark-results.json"
    data = run()
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"wrote {out_path}")
