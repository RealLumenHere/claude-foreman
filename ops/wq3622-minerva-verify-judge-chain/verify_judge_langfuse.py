"""wq#3622 — prove one verify-judge call on the new rung actually lands in
Langfuse, after install.sh restarts minerva-proposal.service. Re-runnable
evidence, same read-back shape as ops/wq1554-langfuse-lag-monitor/probe_live.py
(GET /api/public/observations, not a guessed endpoint).

Usage (run as lumen, after install.sh's restart):
    python3 verify_judge_langfuse.py [--since-minutes N] [--model deepseek-v4-flash]

Exits 0 and prints the observation id/timestamp when a GENERATION on --model
is found with startTime >= now - since-minutes. Exits 1 on no creds, 2 on none
found (judge chain may still be fail-closed-silent), 3 on an API error. Prints
only status codes, ids, and timestamps — never credentials (masking.py not
needed here since nothing it reads is ever echoed).
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 os.pardir, os.pardir, "harness"))
from langfuse_client import load_creds

DEFAULT_ENV = "/home/lumen/.secrets/langfuse-foreman.env"


def get(url, pk, sk, timeout=10):
    headers = {"Authorization": "Basic " + base64.b64encode(f"{pk}:{sk}".encode()).decode()}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-minutes", type=int, default=30)
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--env", default=DEFAULT_ENV)
    args = ap.parse_args()

    host, pk, sk = load_creds(args.env)
    if not host:
        print(f"no Langfuse creds at {args.env}")
        return 1
    base = host.rstrip("/")

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=args.since_minutes)
    url = f"{base}/api/public/observations?type=GENERATION&limit=50"
    code, body = get(url, pk, sk)
    if code != 200:
        print(f"observations-list: HTTP {code}")
        return 3

    try:
        rows = json.loads(body).get("data", [])
    except Exception as e:
        print(f"observations-list: bad JSON ({type(e).__name__})")
        return 3

    for row in rows:
        model = row.get("model") or (row.get("modelParameters") or {}).get("model")
        start = row.get("startTime", "")
        if model != args.model:
            continue
        try:
            ts = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except Exception:
            continue
        if ts >= cutoff:
            print(f"FOUND: observation {row.get('id')} model={model} startTime={start}")
            return 0

    print(f"NOT FOUND: no GENERATION on model={args.model} since {cutoff.isoformat()}")
    print("(the judge chain may still be failing closed before it ever reaches LiteLLM's")
    print(" Langfuse callback -- check journalctl -u minerva-proposal for judge_err/fail_closed)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
