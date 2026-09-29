"""wq#3124 live design probe: verify against the LIVE Langfuse that the canary
read-back path actually works (ingest -> GET /api/public/traces/{id} -> 200),
instead of trusting version-doc tables (the exact failure class wq#3124 fixed:
a monitor written against /api/public/metrics/export-lag, an endpoint guessed
from a version string that never existed). Re-runnable acceptance evidence for
the monitor's endpoint choice; exits 0 only when a freshly ingested canary
becomes visible within 90s. Prints ONLY status codes, version strings, and
booleans — never credentials.

2026-09-29 run against v4.32.0: health 200, traces-list 200, ingest ok,
trace-by-id 404 at +0s/+5s then 200 at +10s.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, os.pardir, "harness"))
from langfuse_client import load_creds, ingest


def get(url, pk=None, sk=None, timeout=10):
    headers = {}
    if pk:
        import base64
        headers["Authorization"] = "Basic " + base64.b64encode(f"{pk}:{sk}".encode()).decode()
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return None, type(e).__name__


def main():
    host, pk, sk = load_creds(os.getenv("LANGFUSE_ENV", "/home/lumen/.secrets/langfuse-foreman.env"))
    if not host:
        print("no creds")
        return 1
    base = host.rstrip("/")

    code, body = get(base + "/api/public/health")
    ver = ""
    try:
        ver = json.loads(body).get("version", "")
    except Exception:
        pass
    print(f"health: {code} version={ver}")

    code, _ = get(base + "/api/public/traces?limit=1", pk, sk)
    print(f"traces-list: {code}")

    trace_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    batch = [{"id": str(uuid.uuid4()), "type": "trace-create", "timestamp": now,
              "body": {"id": trace_id, "name": "wq3124-probe-canary", "timestamp": now,
                       "metadata": {"probe": "wq3124"}, "tags": ["wq3124-probe"]}}]
    try:
        ingest(host, pk, sk, batch)
        print("ingest: ok")
    except Exception as e:
        print(f"ingest: FAIL {type(e).__name__}")
        return 1

    deadline = time.time() + 90
    while time.time() < deadline:
        code, _ = get(f"{base}/api/public/traces/{trace_id}", pk, sk)
        print(f"trace-by-id: {code} at +{int(90 - (deadline - time.time()))}s")
        if code == 200:
            print("read-back: VISIBLE")
            return 0
        time.sleep(5)
    print("read-back: NOT VISIBLE within 90s")
    return 2


if __name__ == "__main__":
    sys.exit(main())
