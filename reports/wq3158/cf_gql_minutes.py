"""wq#3158: per-minute lower bound on long (>=60 s) mcpw Worker invocations.

workersInvocationsAdaptive has no wallTime filter, so group status=success by
minute and count minutes whose p99.9 wall time is >= 60 s: each such minute
holds at least one long call. Buckets: ~70 s (TOOLS_CALL_TIMEOUT_MS abort) and
~75 s. Prints aggregates only, never the token.
Usage: cf_gql_minutes.py SINCE UNTIL  (RFC3339 UTC)
"""
import collections
import json
import os
import sys
import urllib.error
import urllib.request

TOKEN_FILE = os.path.expanduser("~/.cf_workers_token")
API = "https://api.cloudflare.com/client/v4"

Q = """query($a:String!,$s:Time!,$u:Time!){viewer{accounts(filter:{accountTag:$a}){
  workersInvocationsAdaptive(limit:10000,orderBy:[datetimeMinute_ASC],filter:{
    datetime_geq:$s,datetime_lt:$u,scriptName:"lumen-mcp",status:"success"}){
    sum{requests}
    quantiles{wallTimeP999}
    dimensions{datetimeMinute}}}}}"""


def req(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Authorization", "Bearer " + token)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        return json.load(e)


def bucket(sec):
    if sec >= 74:
        return "75s"
    if sec >= 69:
        return "70s"
    return "60-69s"


def main():
    since, until = sys.argv[1], sys.argv[2]
    token = open(TOKEN_FILE).read().strip()
    acct = req("GET", "/accounts", token)["result"][0]["id"]
    j = req("POST", "/graphql", token, {"query": Q, "variables": {"a": acct, "s": since, "u": until}})
    for e in j.get("errors") or []:
        print("gql error", e.get("message"))
    rows = ((j.get("data") or {}).get("viewer") or {}).get("accounts", [{}])[0].get(
        "workersInvocationsAdaptive") or []
    counts = collections.Counter()
    hist = collections.Counter()
    for r in rows:
        sec = r["quantiles"]["wallTimeP999"] / 1e6
        if sec >= 60:
            counts[bucket(sec)] += 1
            hist[int(sec)] += 1
    print("window", since, until, "success_minutes", len(rows),
          "requests", sum(r["sum"]["requests"] for r in rows))
    print("minutes_with_long_call", sum(counts.values()), dict(counts))
    print("p999_seconds_hist", dict(sorted(hist.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
