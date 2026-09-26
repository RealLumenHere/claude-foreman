#!/usr/bin/env python3
# wq#1789 pilot 992fcdd0fdfc: conformal cascade router.
# Calls a weak (local) model first; escalates to a strong (remote) model only
# when the weak answer's conformal nonconformity score exceeds a calibrated
# threshold. Client only -- talks to the LiteLLM gateway on localhost:4000.
import json, math, sys, time, urllib.request

WEAK_MODEL = "lumen-lfm2-1b"
STRONG_MODEL = "deepseek-v4-flash"
GATEWAY = "http://localhost:4000/v1/chat/completions"


def _load_key(env_path="/home/lumen/agent/.env"):
    for line in open(env_path):
        if line.startswith("LITELLM_MASTER_KEY="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("LITELLM_MASTER_KEY not found in " + env_path)


def call_model(model, question, key, timeout=60, retries=4):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Answer with only the answer, no explanation."},
            {"role": "user", "content": question},
        ],
        "max_tokens": 24,
        "temperature": 0,
        "logprobs": True,
        "top_logprobs": 5,
    }
    req = urllib.request.Request(
        GATEWAY,
        json.dumps(body).encode(),
        {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    last_err = None
    for attempt in range(retries):
        t0 = time.time()
        try:
            j = json.load(urllib.request.urlopen(req, timeout=timeout))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            raise
        dt = time.time() - t0
        choice = j["choices"][0]
        logprobs = [x["logprob"] for x in ((choice.get("logprobs") or {}).get("content") or [])]
        answer = (choice["message"].get("content") or "").strip()
        return {"answer": answer, "latency": dt, "logprobs": logprobs}
    raise last_err


def nonconformity_score(logprobs):
    """1 - mean token probability. Higher = less conformal / less confident."""
    if not logprobs:
        return 1.0
    mean_logprob = sum(logprobs) / len(logprobs)
    return 1.0 - math.exp(mean_logprob)


def route(question, key, threshold):
    """Conformal cascade: answer with WEAK_MODEL; escalate to STRONG_MODEL
    when the weak answer's nonconformity score exceeds `threshold` (the
    value calibrated on a held-out split so that P(score > threshold |
    weak answer wrong) is bounded -- the conformal guarantee)."""
    weak = call_model(WEAK_MODEL, question, key)
    score = nonconformity_score(weak["logprobs"])
    if score <= threshold:
        return {"model_used": WEAK_MODEL, "answer": weak["answer"],
                "latency": weak["latency"], "score": score, "escalated": False}
    strong = call_model(STRONG_MODEL, question, key)
    return {"model_used": STRONG_MODEL, "answer": strong["answer"],
            "latency": weak["latency"] + strong["latency"], "score": score,
            "escalated": True}


def calibrate(rows, key, target_miscoverage=0.1):
    """Compute the nonconformity threshold on a calibration split: the
    (1 - target_miscoverage) quantile of scores for weak-model answers that
    were WRONG, so escalation catches roughly that fraction of failures."""
    wrong_scores = []
    for r in rows:
        weak = call_model(WEAK_MODEL, key=key, question=r["q"])
        score = nonconformity_score(weak["logprobs"])
        if not _is_correct(weak["answer"], r["aliases"]):
            wrong_scores.append(score)
    if not wrong_scores:
        return 0.5
    wrong_scores.sort()
    idx = max(0, int(len(wrong_scores) * target_miscoverage) - 1)
    return wrong_scores[idx]


def _is_correct(answer, aliases):
    norm = answer.strip().lower()
    return any(a in norm for a in aliases)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("baseline", "treatment"):
        print("usage: conformal_cascade.py baseline|treatment", file=sys.stderr)
        sys.exit(1)
    print("driver mode:", sys.argv[1], "-- see run.py for the full baseline/treatment harness")
