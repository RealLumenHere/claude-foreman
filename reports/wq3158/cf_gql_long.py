"""wq#3158: long-wall-time invocations + recent deployments for the mcpw workers.

Counts invocations whose wall time is >= 60 s (the 70 s tools/call abort
lives inside the Worker, so those requests end as status=success with a
long wall time) and lists deployment timestamps to line up the exception
clusters. Prints aggregates only, never the token.
"""
import json
import os
import sys
import urllib.error
import urllib.request

TOKEN_FILE = os.path.expanduser("~/.cf_workers_token")
API = "https://api.cloudflare.com/client/v4"
SINCE = "2026-09-29T19:02:00Z"
UNTIL = "2026-10-01T19:02:00Z"

Q = """query($a:String!,$s:Time!,$u:Time!){viewer{accounts(filter:{accountTag:$a}){
  workersInvocationsAdaptive(limit:500,orderBy:[datetimeHour_ASC],filter:{
    datetime_geq:$s,datetime_lt:$u,scriptName_in:["lumen-mcp","lumen-mcp-v2"],
    status:"success"}){
    sum{requests}
    quantiles{wallTimeP99 wallTimeP999}
    dimensions{datetimeHour scriptName status}}}}}"""


def req(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Authorization", "Bearer " + token)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}


def main():
    token = open(TOKEN_FILE).read().strip()
    acct = req("GET", "/accounts", token)[1]["result"][0]["id"]
    st, j = req("POST", "/graphql", token, {"query": Q, "variables": {"a": acct, "s": SINCE, "u": UNTIL}})
    for e in j.get("errors") or []:
        print("gql error", e.get("message"))
    rows = ((j.get("data") or {}).get("viewer") or {}).get("accounts", [{}])[0].get(
        "workersInvocationsAdaptive") or []
    hours_long = 0
    for r in rows:
        d, q = r["dimensions"], r["quantiles"]
        if q["wallTimeP999"] >= 60e6:
            hours_long += 1
            print("long", d["datetimeHour"], d["scriptName"], r["sum"]["requests"],
                  "p99_s=%.1f" % (q["wallTimeP99"] / 1e6), "p999_s=%.1f" % (q["wallTimeP999"] / 1e6))
    print("success_hour_rows", len(rows), "hours_with_p999_ge_60s", hours_long)
    for script in ("lumen-mcp", "lumen-mcp-v2"):
        st, j = req("GET", "/accounts/%s/workers/scripts/%s/deployments" % (acct, script), token)
        deps = (j.get("result") or {}).get("deployments") or []
        print("deployments", script, st, [d.get("created_on") for d in deps[:6]],
              [e.get("message") for e in j.get("errors") or []])
    return 0


if __name__ == "__main__":
    sys.exit(main())
