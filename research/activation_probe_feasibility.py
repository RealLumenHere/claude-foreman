#!/usr/bin/env python3
"""wq#1716 — re-run the four live reads behind research/ACTIVATION_PROBES.md.

Read-only HTTP against the Isis local rung (bridge-safe: no ICMP, no router IPs).
Prints the grade-A facts the doc cites so a reviewer can reproduce them.

    python3 research/activation_probe_feasibility.py

The prefill measurement sends a 1,000-token prompt with cache_prompt disabled and
n_predict=1, so it costs one prefill (~60 s on Isis) and one token of generation.
"""
import json
import time
import urllib.error
import urllib.request

LLAMA = "http://100.100.129.24:8095"
OLLAMA = "http://100.100.129.24:11434"
MODEL = "qwen3.6:35b-a3b"


def post(url, payload, timeout=300):
    req = urllib.request.Request(
        url, json.dumps(payload).encode(), {"Content-Type": "application/json"}
    )
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as e:
        return json.load(e)


def get(url, timeout=30):
    return json.load(urllib.request.urlopen(url, timeout=timeout))


def main():
    props = get(LLAMA + "/props")
    print("build_info      :", props.get("build_info"))
    print("model_path      :", props.get("model_path"))
    print("n_ctx           :", props.get("default_generation_settings", {}).get("n_ctx"))
    print("total_slots     :", props.get("total_slots"))
    print("modalities      :", props.get("modalities"))

    tags = get(OLLAMA + "/api/tags")
    print("local catalogue :", [
        (m["name"], m["details"]["parameter_size"], m["details"]["quantization_level"])
        for m in tags["models"]
    ])

    info = post(OLLAMA + "/api/show", {"model": MODEL}, timeout=60)["model_info"]
    geom = {k: v for k, v in info.items() if k.startswith("qwen35moe.") and "vision" not in k}
    print("block_count     :", geom.get("qwen35moe.block_count"))
    print("embedding_length:", geom.get("qwen35moe.embedding_length"))
    print("experts         :", geom.get("qwen35moe.expert_count"),
          "used", geom.get("qwen35moe.expert_used_count"))

    emb = post(LLAMA + "/embeddings", {"input": "probe"}, timeout=60)
    print("/embeddings     :", emb.get("error", emb))

    lp = post(LLAMA + "/completion", {
        "prompt": "The capital of France is", "n_predict": 3,
        "n_probs": 4, "cache_prompt": False,
    })
    first = lp["completion_probabilities"][0]
    print("logprobs        :", first["token"], round(first["logprob"], 4),
          "top", [t["token"] for t in first["top_logprobs"]])

    t0 = time.time()
    pf = post(LLAMA + "/completion", {
        "prompt": "banana " * 1000, "n_predict": 1, "cache_prompt": False,
    })
    wall = time.time() - t0
    tm = pf["timings"]
    print("prefill         : prompt_n %d  %.2f ms/tok  %.2f tok/s  wall %.1f s" % (
        tm["prompt_n"], tm["prompt_per_token_ms"], tm["prompt_per_second"], wall))


if __name__ == "__main__":
    main()
