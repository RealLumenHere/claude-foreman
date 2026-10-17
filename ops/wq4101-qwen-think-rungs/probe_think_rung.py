#!/usr/bin/env python3
"""probe_think_rung (wq#4101) — test-to-failure acceptance probe for a gateway
alias that fronts a thinking model.

Contract (the exact silent failure wq#4101 found): a 200-token completion must
return NON-EMPTY content, or — if it truncated — say so with
finish_reason=length. Empty content with finish_reason=stop is the defect:
hidden thinking ate the budget and the truncation was masked.

PASS (0): content non-empty, or empty with finish_reason=length (truncation
          is at least visible to the caller).
FAIL (1): content empty and finish_reason=stop — the wq#4101 class.
ERROR (2): transport/HTTP failure.

The key is read from a 0600 file and sent only as a header, never printed or
put on a command line (instinct 4). Output is lengths + finish_reason only.

Usage:
  probe_think_rung.py [--model lumen/code] [--base http://localhost:4000]
                      [--key-file /home/lumen/.secrets/foreman-litellm.token]
                      [--max-tokens 200]
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

PROMPT = "Write a python one-liner that reverses a string. Reply with code only."


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="lumen/code")
    p.add_argument("--base", default="http://localhost:4000")
    p.add_argument("--key-file", default="/home/lumen/.secrets/foreman-litellm.token")
    p.add_argument("--max-tokens", type=int, default=200)
    args = p.parse_args(argv)

    try:
        with open(args.key_file) as f:
            key = f.read().strip()
    except OSError as e:
        print(f"ERROR: cannot read key file: {e.strerror}")
        return 2

    body = json.dumps({
        "model": args.model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": args.max_tokens,
    }).encode()
    req = urllib.request.Request(
        f"{args.base}/v1/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"ERROR: HTTP {e.code} from gateway")
        return 2
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"ERROR: {type(e).__name__}")
        return 2

    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    finish = choice.get("finish_reason")
    usage = resp.get("usage") or {}
    print(f"model={args.model} content_chars={len(content)} "
          f"reasoning_chars={len(reasoning)} finish_reason={finish} "
          f"completion_tokens={usage.get('completion_tokens')}")

    if content.strip():
        print("PASS: non-empty content")
        return 0
    if finish == "length":
        print("PASS (degraded): empty but truncation is visible (finish_reason=length)")
        return 0
    print("FAIL: empty content with finish_reason != length — wq#4101 class "
          "(hidden thinking ate the budget, truncation masked)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
