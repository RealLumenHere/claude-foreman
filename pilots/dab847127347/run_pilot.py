#!/usr/bin/env python3
"""Pilot runner for dab847127347: does flash attention + an 8-bit/4-bit
KV-cache (OLLAMA_KV_CACHE_TYPE=q8_0/q4_0) improve prefill throughput on a
long-context prompt versus Ollama's default KV cache, on Lumen's own
hardware/models?

Per CARD.md: one synthetic ~4k-token document (5 planted facts at
10/30/50/70/90% depth) built deterministically from random.Random(1234),
5 fixed questions (one per fact), temperature 0, seed 1, num_ctx 8192,
num_predict 16. Each arm is a separate ollama server instance the caller
starts with the arm's env vars before invoking this script; the script
only talks to the given --base-url and never touches the env itself, so
production ollama (port 11434) is never in the loop.

Usage:
    run_pilot.py build-sample --out sample.json
    run_pilot.py run-arm --arm baseline --base-url http://127.0.0.1:11435 \
        --model llama3.1:8b --sample sample.json --out results/baseline.json
"""
import argparse
import hashlib
import json
import random
import time
import urllib.request

FACT_DEPTHS = (0.10, 0.30, 0.50, 0.70, 0.90)
TARGET_TOKENS = 4000
FILLER_WORDS = (
    "the quick brown fox jumps over a lazy dog near the river while "
    "clouds drift slowly across an open field of amber wheat and "
    "distant hills fade into a pale afternoon haze"
).split()


def build_sample(seed=1234):
    rng = random.Random(seed)
    n_words = TARGET_TOKENS  # ~1 token/word for this filler vocabulary
    words = [rng.choice(FILLER_WORDS) for _ in range(n_words)]

    facts = []
    questions = []
    for i, depth in enumerate(FACT_DEPTHS):
        vault_id = i + 1
        code = "".join(rng.choice("0123456789ABCDEF") for _ in range(6))
        sentence = f"The access code for vault {vault_id} is {code}."
        facts.append({"depth": depth, "vault_id": vault_id, "code": code, "sentence": sentence})
        questions.append(f"What is the access code for vault {vault_id}?")

    for fact in facts:
        pos = int(len(words) * fact["depth"])
        words[pos:pos] = fact["sentence"].split()

    document = " ".join(words)
    sample = {
        "seed": seed,
        "document": document,
        "facts": facts,
        "questions": questions,
        "num_ctx": 8192,
        "num_predict": 16,
        "temperature": 0,
    }
    sample["sha256"] = hashlib.sha256(document.encode()).hexdigest()
    return sample


def _post(base_url, path, payload, timeout=180):
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def run_arm(base_url, model, sample, rounds=3):
    """Runs `rounds` interleaved rounds of the fixed sample against an
    already-running ollama instance at base_url. First question per round
    pays full prefill; the other four reuse the cached prefix."""
    round_results = []
    for r in range(rounds):
        q_results = []
        for qi, question in enumerate(sample["questions"]):
            prompt = sample["document"] + "\n\nQuestion: " + question + "\nAnswer:"
            t0 = time.time()
            resp = _post(
                base_url,
                "/api/generate",
                {
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "num_ctx": sample["num_ctx"],
                        "num_predict": sample["num_predict"],
                        "temperature": sample["temperature"],
                        "seed": 1,
                    },
                },
            )
            wall_s = time.time() - t0
            fact = sample["facts"][qi]
            correct = fact["code"] in resp.get("response", "")
            prompt_eval_count = resp.get("prompt_eval_count", 0)
            prompt_eval_duration_ns = resp.get("prompt_eval_duration", 0)
            eval_count = resp.get("eval_count", 0)
            eval_duration_ns = resp.get("eval_duration", 0)
            q_results.append({
                "question_index": qi,
                "vault_id": fact["vault_id"],
                "correct": correct,
                "wall_s": round(wall_s, 3),
                "prompt_eval_count": prompt_eval_count,
                "prompt_eval_tok_s": (
                    prompt_eval_count / (prompt_eval_duration_ns / 1e9)
                    if prompt_eval_duration_ns else None
                ),
                "eval_count": eval_count,
                "decode_tok_s": (
                    eval_count / (eval_duration_ns / 1e9) if eval_duration_ns else None
                ),
            })
        round_results.append(q_results)
    return round_results


def summarize(round_results):
    prefill = [r[0]["prompt_eval_tok_s"] for r in round_results if r[0]["prompt_eval_tok_s"]]
    decode = [
        q["decode_tok_s"]
        for r in round_results
        for q in r
        if q["decode_tok_s"]
    ]
    n_correct = sum(q["correct"] for r in round_results for q in r)
    n_total = sum(len(r) for r in round_results)
    prefill.sort()
    return {
        "median_prefill_tok_s": prefill[len(prefill) // 2] if prefill else None,
        "median_decode_tok_s": sorted(decode)[len(decode) // 2] if decode else None,
        "needle_accuracy": n_correct / n_total if n_total else None,
        "n_rounds": len(round_results),
    }


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build-sample")
    p_build.add_argument("--out", required=True)

    p_run = sub.add_parser("run-arm")
    p_run.add_argument("--arm", required=True)
    p_run.add_argument("--base-url", required=True)
    p_run.add_argument("--model", required=True)
    p_run.add_argument("--sample", required=True)
    p_run.add_argument("--rounds", type=int, default=3)
    p_run.add_argument("--out", required=True)

    args = ap.parse_args()

    if args.cmd == "build-sample":
        sample = build_sample()
        with open(args.out, "w") as f:
            json.dump(sample, f, indent=2)
        print(json.dumps({"sha256": sample["sha256"], "facts": sample["facts"]}, indent=2))
        return

    with open(args.sample) as f:
        sample = json.load(f)
    round_results = run_arm(args.base_url, args.model, sample, rounds=args.rounds)
    summary = summarize(round_results)
    result = {"arm": args.arm, "model": args.model, "summary": summary, "rounds": round_results}
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
