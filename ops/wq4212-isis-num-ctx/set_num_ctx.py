#!/usr/bin/env python3
"""Pin num_ctx on an ollama tag in place via the HTTP API (wq#4212).

ollama 0.34 on isis loads qwen3.6:35b-a3b at context_length 4096 and silently
truncates longer prompts. LiteLLM's lumen/code and isis-coder-14b both route to
that tag, and the OpenAI-compat /v1 path (wq#4101's planned shape) cannot carry
num_ctx at all, so the context has to live on the tag itself. Rewriting the tag
from itself keeps one loaded copy for every caller (hallucination_guard hits the
same tag directly) instead of a second 22GB variant competing for isis RAM.

  set_num_ctx.py --check                 # show current params, no write
  set_num_ctx.py --apply  [--num-ctx N]  # write; prints the rollback command
  set_num_ctx.py --rollback --num-ctx 4096
"""
import argparse
import json
import sys
import urllib.request

DEFAULT_HOST = "http://100.100.129.24:11434"
DEFAULT_TAG = "qwen3.6:35b-a3b"
DEFAULT_NUM_CTX = 32768


def _post(host, path, body, timeout=600):
    req = urllib.request.Request(host + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def _value(raw):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1]
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def parse_parameters(text):
    """ollama /api/show `parameters` text -> create-API dict. Repeated keys
    (stop) become lists, as the create API expects."""
    params = {}
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        key, _, raw = line.strip().partition(" ")
        val = _value(raw)
        if key in params:
            prev = params[key]
            params[key] = (prev if isinstance(prev, list) else [prev]) + [val]
        elif key == "stop":
            params[key] = [val]
        else:
            params[key] = val
    return params


def build_create(tag, params, num_ctx):
    merged = dict(params)
    merged["num_ctx"] = num_ctx
    return {"model": tag, "from": tag, "parameters": merged, "stream": False}


def show(host, tag):
    return json.loads(_post(host, "/api/show", {"model": tag}, timeout=30))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--tag", default=DEFAULT_TAG)
    ap.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX)
    a = ap.parse_args(argv)

    before = parse_parameters(show(a.host, a.tag).get("parameters"))
    print(json.dumps({"tag": a.tag, "params_before": before}, sort_keys=True))
    if a.check:
        return 0
    if a.rollback and a.num_ctx == DEFAULT_NUM_CTX:
        ap.error("--rollback needs the --num-ctx to restore (4096 was the pre-wq#4212 load)")

    body = build_create(a.tag, before, a.num_ctx)
    resp = _post(a.host, "/api/create", body)
    after = parse_parameters(show(a.host, a.tag).get("parameters"))
    print(json.dumps({"create": resp.strip()[-200:], "params_after": after}, sort_keys=True))
    lost = sorted(k for k in before if k != "num_ctx" and after.get(k) != before[k])
    if after.get("num_ctx") != a.num_ctx or lost:
        print(f"FAIL num_ctx={after.get('num_ctx')} lost_params={lost}", file=sys.stderr)
        return 1
    if a.apply:
        prior = before.get("num_ctx", 4096)
        print(f"rollback: {sys.argv[0]} --rollback --tag {a.tag} --num-ctx {prior}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
