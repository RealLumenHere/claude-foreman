"""wq#3158: is [observability] still enabled on the live mcpw Worker scripts?

lumen-mcp was redeployed 5x on 2026-10-01 after the wq#2925 deploy; a deploy
from a tree without [observability] would switch Workers Logs off again.
Prints the observability block only, never the token.
"""
import json
import os
import sys
import urllib.error
import urllib.request

TOKEN_FILE = os.path.expanduser("~/.cf_workers_token")
API = "https://api.cloudflare.com/client/v4"


def get(path, token):
    r = urllib.request.Request(API + path)
    r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def main():
    token = open(TOKEN_FILE).read().strip()
    acct = get("/accounts", token)[1]["result"][0]["id"]
    for script in ("lumen-mcp", "lumen-mcp-v2"):
        st, j = get("/accounts/%s/workers/scripts/%s/script-settings" % (acct, script), token)
        res = j.get("result") or {}
        print(script, st, "observability=", json.dumps(res.get("observability")),
              [e.get("message") for e in j.get("errors") or []])
    return 0


if __name__ == "__main__":
    sys.exit(main())
