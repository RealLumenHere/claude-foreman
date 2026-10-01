"""wq#3158: fallback attribution via the CF GraphQL Analytics API.

Workers Logs telemetry/query is 403 for the deploy token, so try the
analytics datasets that the same token may be able to read. Prints status,
error messages and aggregated counts only, never the token.
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


def call(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}


def gql(token, query, variables):
    return call("POST", "/graphql", token, {"query": query, "variables": variables})


def gql_errs(j):
    return [e.get("message") for e in (j.get("errors") or [])]


def main():
    token = open(TOKEN_FILE).read().strip()
    st, j = call("GET", "/accounts", token)
    acct = j["result"][0]["id"]
    st, j = call("GET", "/zones", token)
    zones = [(z["id"], z["name"]) for z in (j.get("result") or [])]
    print("zones", st, [n for _, n in zones], [e.get("message") for e in j.get("errors") or []])

    q_inv = """query($a:String!,$s:Time!,$u:Time!){viewer{accounts(filter:{accountTag:$a}){
      workersInvocationsAdaptive(limit:100,filter:{datetime_geq:$s,datetime_lt:$u,
        scriptName_in:["lumen-mcp","lumen-mcp-v2"]}){
        sum{requests errors subrequests} dimensions{scriptName status}}}}}"""
    st, j = gql(token, q_inv, {"a": acct, "s": SINCE, "u": UNTIL})
    print("workersInvocationsAdaptive", st, gql_errs(j))
    try:
        rows = j["data"]["viewer"]["accounts"][0]["workersInvocationsAdaptive"]
        for r in rows:
            print("  inv", r["dimensions"], r["sum"])
    except (KeyError, IndexError, TypeError):
        pass

    q_http = """query($z:String!,$s:Time!,$u:Time!){viewer{zones(filter:{zoneTag:$z}){
      httpRequestsAdaptiveGroups(limit:200,filter:{datetime_geq:$s,datetime_lt:$u,
        clientRequestHTTPHost_like:"%mcp%"}){
        count dimensions{clientRequestHTTPHost clientRequestPath clientRequestHTTPMethodName edgeResponseStatus}}}}}"""
    for zid, zname in zones:
        st, j = gql(token, q_http, {"z": zid, "s": SINCE, "u": UNTIL})
        print("httpRequestsAdaptiveGroups", zname, st, gql_errs(j)[:2])
        try:
            rows = j["data"]["viewer"]["zones"][0]["httpRequestsAdaptiveGroups"]
        except (KeyError, IndexError, TypeError):
            rows = []
        rows.sort(key=lambda r: (r["dimensions"]["clientRequestPath"], r["dimensions"]["edgeResponseStatus"]))
        for r in rows:
            d = r["dimensions"]
            print("  http", d["clientRequestHTTPHost"], d["clientRequestHTTPMethodName"],
                  d["clientRequestPath"][:60], d["edgeResponseStatus"], r["count"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
