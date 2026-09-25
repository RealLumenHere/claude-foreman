#!/usr/bin/env python3
"""wq#2499: prefill/decode tok/s + loaded size of one ollama node, over HTTP only.

Run once per arm (f16 baseline, then q8_0+FA after the drop-in) and diff the JSON.
Uses ollama's own timers (prompt_eval_duration / eval_duration), so network latency
is excluded. Loaded size comes from /api/ps (weights + KV + compute graph); process
RSS needs a shell on the node and is taken separately (see README.md).

  python3 kvbench.py --host http://100.98.74.87:11434 --model qwen3:14b \
      --label f16 --reps 2 --out f16.json
"""
import argparse
import json
import time
import urllib.request

# ~520 tokens on qwen3's tokenizer; a fixed text so both arms prefill the same thing.
PARA = ("The fleet dispatcher routes build tickets to local inference nodes. Each node "
        "serves one model family through ollama or llama-server, reports health on a "
        "probe path, and is held warm or cold by a power policy. Prefill throughput "
        "bounds how long a worker waits before the first token of a long prompt. ")


def call(host, path, body=None, timeout=1800):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(host + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def run_once(host, model, rep, num_predict):
    # rep in the prompt defeats ollama's prompt cache so every rep prefills in full.
    prompt = f"Run {rep} at {time.time():.0f}.\n" + PARA * 8 + "\nSummarise the above in detail."
    r = call(host, "/api/generate", {
        "model": model, "prompt": prompt, "stream": False, "think": False,
        "options": {"num_predict": num_predict, "temperature": 0, "seed": 1},
    })
    pe_n, pe_ns = r["prompt_eval_count"], r["prompt_eval_duration"]
    ev_n, ev_ns = r["eval_count"], r["eval_duration"]
    return {
        "prompt_tokens": pe_n, "prefill_tps": round(pe_n / (pe_ns / 1e9), 2),
        "gen_tokens": ev_n, "decode_tps": round(ev_n / (ev_ns / 1e9), 2),
        "load_s": round(r.get("load_duration", 0) / 1e9, 1),
        "total_s": round(r["total_duration"] / 1e9, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--num-predict", type=int, default=64)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    runs = [run_once(a.host, a.model, i, a.num_predict) for i in range(a.reps)]
    ps = [m for m in call(a.host, "/api/ps")["models"] if m["name"].startswith(a.model)]
    warm = runs[1:] or runs  # rep 0 pays the model load
    res = {
        "label": a.label, "host": a.host, "model": a.model,
        "version": call(a.host, "/api/version")["version"],
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runs": runs,
        "prefill_tps": round(sum(r["prefill_tps"] for r in warm) / len(warm), 2),
        "decode_tps": round(sum(r["decode_tps"] for r in warm) / len(warm), 2),
        "loaded_size_gib": round(ps[0]["size"] / 2**30, 2) if ps else None,
        "context_length": ps[0].get("context_length") if ps else None,
    }
    with open(a.out, "w") as f:
        json.dump(res, f, indent=2)
    print(json.dumps({k: res[k] for k in ("label", "prefill_tps", "decode_tps",
                                           "loaded_size_gib", "context_length")}))


if __name__ == "__main__":
    main()
