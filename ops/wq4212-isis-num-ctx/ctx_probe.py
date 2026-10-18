#!/usr/bin/env python3
"""Test-to-failure gate for wq#4212: does a long prompt reach the model whole?

Sends an N-word prompt straight to ollama /api/chat (the native path LiteLLM's
ollama/ provider uses) and compares the evaluated prompt tokens with the word
count. Every probe word is a distinct numbered token run, so an untruncated
prompt evaluates to at least N tokens; ollama's silent truncation shows up as
prompt_eval_count < N (wq#4212: 8000 words evaluated 2050 tokens).

Exit 0 PASS, 1 FAIL (truncated), 2 transport error.
"""
import argparse
import json
import sys
import time
import urllib.request

DEFAULT_HOST = "http://100.100.129.24:11434"
DEFAULT_TAG = "qwen3.6:35b-a3b"


def build_prompt(words):
    body = " ".join(f"w{i}" for i in range(words))
    return f"Reply with the single word OK.\n{body}\nReply with the single word OK."


def verdict(words, prompt_tokens):
    return "PASS" if prompt_tokens >= words else "FAIL"


def probe(host, tag, words, timeout):
    req = {"model": tag, "stream": False, "think": False,
           "messages": [{"role": "user", "content": build_prompt(words)}],
           "options": {"num_predict": 1}}
    r = urllib.request.Request(host + "/api/chat", data=json.dumps(req).encode(),
                               headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        out = json.loads(resp.read())
    return out.get("prompt_eval_count", 0), time.time() - t0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--tag", default=DEFAULT_TAG)
    ap.add_argument("--words", type=int, default=3000)
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args(argv)
    try:
        tokens, secs = probe(a.host, a.tag, a.words, a.timeout)
    except OSError as e:
        print(json.dumps({"verdict": "ERROR", "error": str(e)}))
        return 2
    v = verdict(a.words, tokens)
    print(json.dumps({"verdict": v, "words": a.words, "prompt_eval_count": tokens,
                      "seconds": round(secs, 1), "tag": a.tag}))
    return 0 if v == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
