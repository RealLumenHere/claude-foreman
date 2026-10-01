"""wq#3158: probe whether the cf-workers token can read Workers Logs.

Prints HTTP status + CF error codes/messages only, never the token.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

TOKEN_FILE = os.path.expanduser("~/.cf_workers_token")
API = "https://api.cloudflare.com/client/v4"


def call(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}


def errs(j):
    return [(e.get("code"), e.get("message")) for e in (j.get("errors") or [])]


def main():
    token = open(TOKEN_FILE).read().strip()
    st, j = call("GET", "/user/tokens/verify", token)
    print("verify", st, (j.get("result") or {}).get("status"), errs(j))
    st, j = call("GET", "/accounts", token)
    accts = [a["id"] for a in (j.get("result") or [])]
    print("accounts", st, len(accts), errs(j))
    if not accts:
        return 1
    acct = accts[0]
    st, j = call("GET", "/accounts/%s/workers/scripts" % acct, token)
    print("scripts", st, sorted(s.get("id") for s in (j.get("result") or [])), errs(j))
    now = int(time.time() * 1000)
    q = {
        "queryId": "wq3158-probe",
        "timeframe": {"from": now - 3600 * 1000, "to": now},
        "view": "calculations",
        "parameters": {"calculations": [{"operator": "count"}]},
    }
    st, j = call("POST", "/accounts/%s/workers/observability/telemetry/query" % acct, token, q)
    print("telemetry/query", st, errs(j))
    st, j = call("POST", "/accounts/%s/workers/observability/telemetry/keys" % acct, token,
                 {"timeframe": {"from": now - 3600 * 1000, "to": now}})
    print("telemetry/keys", st, errs(j))
    return 0


if __name__ == "__main__":
    sys.exit(main())
