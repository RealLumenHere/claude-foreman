#!/usr/bin/env python3
"""spec_decode.py -- pilot 1a025ddf9834 (wq#1951): hybrid autoregressive-speculative
inference for local models.

LiteLLM's OpenAI-compatible /chat/completions endpoint does not expose per-token
logits for local models (lumen-lfm2-1b, qwen36-local), so true token-level
speculative decoding (accept/reject against the target's own logit distribution)
is not implementable against this gateway. This wrapper implements the
practically-reachable variant: sequence-level draft-then-verify.

  1. DRAFT: the small model (lumen-lfm2-1b) generates a full candidate response.
  2. VERIFY: the target model (qwen36-local) is asked, in one pass, to either
     accept the draft verbatim or emit a corrected response. One target call
     either way -- the draft is free extra work, never a second full
     autoregressive target pass on accept.
  3. Baseline is the target model answering the prompt directly, no draft.

Hypothesis under test: draft-then-verify reduces observed wall-clock latency
for local-model completions vs. plain autoregressive baseline, at output
quality parity (judged by exact/near-exact match rate against baseline text).
"""
import json
import time
import urllib.request

import sys
sys.path.insert(0, "/home/lumen/foreman/judge")
from judge_common import LITELLM_BASE, litellm_key  # noqa: E402

DRAFT_MODEL = "lumen-lfm2-1b"
TARGET_MODEL = "qwen36-local"


def _chat(model, messages, key, timeout=60, max_tokens=200):
    req = urllib.request.Request(
        f"{LITELLM_BASE}/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
        }).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())
    return body["choices"][0]["message"]["content"]


def baseline_call(prompt, key=None):
    key = key or litellm_key()
    t0 = time.monotonic()
    text = _chat(TARGET_MODEL, [{"role": "user", "content": prompt}], key)
    return {"text": text, "latency_s": time.monotonic() - t0}


def speculative_call(prompt, key=None):
    """Draft-then-verify: draft model proposes, target model accepts or corrects
    in a single call. Returns latency, final text, and whether the draft was
    accepted verbatim (accepted=True means the target's one call was cheap
    confirmation rather than a from-scratch generation)."""
    key = key or litellm_key()
    t0 = time.monotonic()
    draft = _chat(DRAFT_MODEL, [{"role": "user", "content": prompt}], key,
                  max_tokens=200)
    verify_prompt = (
        "You are verifying a draft answer from a smaller, faster model. "
        "If the draft correctly and completely answers the question, reply "
        "with EXACTLY the draft text unchanged. If it is wrong or incomplete, "
        "reply with the corrected answer only (no commentary).\n\n"
        f"Question: {prompt}\n\nDraft answer: {draft}"
    )
    final = _chat(TARGET_MODEL, [{"role": "user", "content": verify_prompt}], key)
    latency = time.monotonic() - t0
    accepted = final.strip() == draft.strip()
    return {"text": final, "latency_s": latency, "draft": draft,
            "accepted_verbatim": accepted}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--mode", choices=["baseline", "treatment"], default="treatment")
    args = ap.parse_args()
    fn = baseline_call if args.mode == "baseline" else speculative_call
    print(json.dumps(fn(args.prompt), indent=2))
