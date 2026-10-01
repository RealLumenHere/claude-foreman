"""wq#3158: per-minute timeline of non-success Worker invocations.

Uses workersInvocationsAdaptive (Account Analytics Read, which the deploy
token has). Prints aggregated rows only, never the token.
Usage: cf_gql_timeline.py SINCE UNTIL  (RFC3339 UTC)
"""
import json
import os
import sys
import urllib.error
import urllib.request

TOKEN_FILE = os.path.expanduser("~/.cf_workers_token")
API = "https://api.cloudflare.com/client/v4"

Q = """query($a:String!,$s:Time!,$u:Time!){viewer{accounts(filter:{accountTag:$a}){
  workersInvocationsAdaptive(limit:500,orderBy:[datetimeMinute_ASC],filter:{
    datetime_geq:$s,datetime_lt:$u,scriptName_in:["lumen-mcp","lumen-mcp-v2"],
    status_neq:"success"}){
    sum{requests}
    quantiles{wallTimeP50 wallTimeP99}
    dimensions{datetimeMinute scriptName status}}}}}"""


def post(path, token, body):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def get(path, token):
    req = urllib.request.Request(API + path)
    req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    since, until = sys.argv[1], sys.argv[2]
    token = open(TOKEN_FILE).read().strip()
    acct = get("/accounts", token)["result"][0]["id"]
    j = post("/graphql", token, {"query": Q, "variables": {"a": acct, "s": since, "u": until}})
    for e in j.get("errors") or []:
        print("error", e.get("message"))
    rows = (j.get("data") or {}).get("viewer", {}).get("accounts", [{}])[0].get(
        "workersInvocationsAdaptive") or []
    for r in rows:
        d, q = r["dimensions"], r["quantiles"]
        print(d["datetimeMinute"], d["scriptName"], d["status"], r["sum"]["requests"],
              "wall_p50_ms=%d" % (q["wallTimeP50"] / 1000), "wall_p99_ms=%d" % (q["wallTimeP99"] / 1000))
    print("rows", len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
