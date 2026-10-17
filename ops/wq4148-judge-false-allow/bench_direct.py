#!/usr/bin/env python3
"""wq#4148 -- run the judge eval set straight against a local Ollama, no gateway.

calibrate_local_judge.py (wq#4103) needs the LiteLLM service key, which a build
worker cannot read, so the false-allow gate could only be run inside a window op.
This drives the same SYSTEM_PROMPT / build_user_prompt / parse_verdict from
judge_common against Ollama's native /api/chat (format=json, temperature 0 -- what
LiteLLM's ollama/ provider sends for response_format=json_object), so a worker can
measure a candidate model or prompt and get re-runnable evidence.

  bench_direct.py --model qwen2.5-coder:7b --slice deny --out /tmp/x.jsonl
  bench_direct.py --model qwen2.5-coder:7b --slice all --judge-fn judge_verdict

--judge-fn raw     : one model call, the pre-wq#4148 path
--judge-fn judge_verdict : judge_common.judge_verdict (deterministic floor + model
                     + deny-wins second look), the production path after wq#4148

Prints false_allow / near_miss / parse_fail on should-deny cases and
allow_retained on should-allow cases; exit 1 on any false-allow.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "judge"))
import judge_common  # noqa: E402

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


def ollama_chat(model, messages, max_tokens=400, timeout=600):
    payload = {"model": model, "messages": messages, "stream": False, "format": "json",
               "options": {"temperature": 0, "num_predict": max_tokens}}
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["message"]["content"]


def ollama_generate(model, messages, max_tokens=400, timeout=600):
    """What the gateway's `ollama/` provider does (ollama journal: every
    172.20.0.2 judge request is POST /api/generate): the messages are flattened
    into one '### System:/### User:' prompt, so the model's chat template wraps
    the whole thing as a single user turn and the rubric never lands in the
    system slot."""
    prompt = "".join(f"### {m['role'].capitalize()}:\n{m['content']}\n\n" for m in messages)
    payload = {"model": model, "prompt": prompt, "stream": False, "format": "json",
               "options": {"temperature": 0, "num_predict": max_tokens}}
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["response"]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", default="qwen2.5-coder:7b")
    p.add_argument("--eval", default=os.path.join(HERE, "..", "..", "judge", "judge_eval_set.jsonl"))
    p.add_argument("--slice", choices=["deny", "allow", "all"], default="deny")
    p.add_argument("--ids", default="", help="comma-separated case ids (overrides --slice)")
    p.add_argument("--judge-fn", choices=["raw", "judge_verdict"], default="judge_verdict")
    p.add_argument("--transport", choices=["chat", "generate"], default="chat",
                   help="chat = /api/chat (ollama_chat/ provider); generate = the "
                        "flattened /api/generate prompt the ollama/ provider sends")
    p.add_argument("--out", required=True, help="per-case JSONL (appended)")
    args = p.parse_args(argv)
    send = ollama_chat if args.transport == "chat" else ollama_generate

    cases = judge_common.load_eval_set(args.eval)
    if args.ids:
        want = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in want]
    elif args.slice != "all":
        cases = [c for c in cases if c["expected_verdict"] == args.slice]

    def chat(messages, max_tokens=400):
        return send(args.model, messages, max_tokens)

    fa, nm, pf, allow_kept, n_allow = [], [], [], 0, 0
    with open(args.out, "a") as out:
        for c in cases:
            t0 = time.time()
            try:
                if args.judge_fn == "raw":
                    raw = chat([{"role": "system", "content": judge_common.SYSTEM_PROMPT},
                                {"role": "user", "content": judge_common.build_user_prompt(c)}])
                    v, err = judge_common.parse_verdict(raw)
                    stage = "model"
                else:
                    v, err, stage = judge_common.judge_verdict(c, chat)
            except Exception as e:
                v, err, stage = None, f"{type(e).__name__}: {e}", "error"
            got = v["verdict"] if v else None
            row = {"id": c["id"], "expected": c["expected_verdict"], "got": got,
                   "tier": v["tier"] if v else None, "stage": stage, "err": err,
                   "latency_s": round(time.time() - t0, 1), "model": args.model,
                   "judge_fn": args.judge_fn, "transport": args.transport}
            out.write(json.dumps(row) + "\n")
            out.flush()
            print(f"{c['id']:14s} exp={c['expected_verdict']:5s} got={str(got):22s} "
                  f"stage={stage:8s} {row['latency_s']}s", flush=True)
            if c["expected_verdict"] == "deny":
                if got is None:
                    pf.append(c["id"])
                elif got == "allow":
                    fa.append(c["id"])
                elif got == "allow-with-conditions":
                    nm.append(c["id"])
            else:
                n_allow += 1
                allow_kept += got in ("allow", "allow-with-conditions")
    n_deny = len(cases) - n_allow
    print(f"should_deny={n_deny} false_allow={len(fa)} near_miss={len(nm)} parse_fail={len(pf)} "
          f"allow_retained={allow_kept}/{n_allow}")
    if fa:
        print("FALSE-ALLOW: " + ", ".join(fa))
    return 1 if fa else 0


if __name__ == "__main__":
    sys.exit(main())
