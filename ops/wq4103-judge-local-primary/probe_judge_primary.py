#!/usr/bin/env python3
"""wq#4103 -- prove the running gateway serves lumen/judge from the local primary
(no hathor timeout, no fallback hop).

Sends one lumen/judge completion and checks three things that together mean the
10s-per-call symptom is gone:
  * HTTP 200 and a parseable verdict came back,
  * x-litellm-attempted-fallbacks is "0" (served by the primary deployment, not
    reached via a fallback -- this is the inverse of the pre-fix header, which
    was always 1), and
  * latency is under --max-latency (default 8s; the pre-fix path measured 11-20s
    because it paid hathor's full 10s timeout before falling back).

  probe_judge_primary.py [--max-latency 8] [--base URL]

Exit 0 pass, 1 fail. Runs where the gateway + service key are reachable.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

CASE = {"action_kind": "bash",
        "command": "ls /home/lumen/kb",
        "context": "read-only directory listing, no side effects"}


def probe(base: str, key: str, max_latency: float) -> tuple[int, str]:
    sys.path.insert(0, "/home/lumen/foreman/judge")
    from judge_common import SYSTEM_PROMPT, build_user_prompt, parse_verdict

    payload = {
        "model": "lumen/judge",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": build_user_prompt(CASE)}],
        "temperature": 0, "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{base}/chat/completions", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=max_latency + 5) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            body = json.loads(resp.read())
    except Exception as e:
        return 1, f"request failed: {type(e).__name__}: {e}"
    latency = time.time() - t0
    attempted = headers.get("x-litellm-attempted-fallbacks", "?")
    raw = body["choices"][0]["message"]["content"]
    verdict, perr = parse_verdict(raw)
    msg = f"latency={latency:.1f}s attempted_fallbacks={attempted} verdict={bool(verdict)}"
    if attempted not in ("0", 0):
        return 1, msg + " -- still reached via fallback, not primary"
    if latency > max_latency:
        return 1, msg + f" -- slower than {max_latency}s budget (hathor timeout still paid?)"
    if not verdict:
        return 1, msg + f" -- no parseable verdict ({perr})"
    return 0, msg + " -- served fast by local primary"


def _key():
    sys.path.insert(0, "/home/lumen/foreman/judge")
    from judge_common import litellm_key
    return litellm_key()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", default="http://127.0.0.1:4000/v1")
    p.add_argument("--max-latency", type=float, default=8.0)
    args = p.parse_args(argv)
    rc, msg = probe(args.base, _key(), args.max_latency)
    print(("PASS: " if rc == 0 else "FAIL: ") + msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
