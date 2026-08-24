#!/usr/bin/env python3
"""wq#949 — per-alias smoke check for a rotated LiteLLM service key.

`litellm-provision-keys --rotate <alias>` writes the replacement under the original
alias's file paths and deletes the old gateway key. This proves the file the consumers
read now holds a key the gateway accepts, and reports the gateway alias it resolved to
so a rotation can be told apart from a no-op:

    python3 smoke_alias.py lumen-agent          # one alias
    python3 smoke_alias.py                      # every alias in SERVICES
    python3 smoke_alias.py --json

Authenticates with the key in the Authorization header and calls /key/info with no query
argument, so the key never enters a URL and never enters this process's output — that is
the leak wq#948 fixed and wq#949 is cleaning up after.
"""
import argparse, json, pathlib, sys, urllib.error, urllib.request

LITELLM = "http://localhost:4000"
SECRETS_DIR = pathlib.Path("/home/lumen/.secrets/litellm-service-keys")


def services():
    """Read the alias list off the provisioning tool so the two can't drift."""
    src = pathlib.Path("/home/lumen/bin/litellm-provision-keys").read_text()
    ns = {}
    body = src.split("SERVICES = {", 1)[1].split("\n}", 1)[0]
    exec("SERVICES = {" + body + "\n}", ns)
    return list(ns["SERVICES"])


def check(alias):
    keyfile = SECRETS_DIR / f"{alias}.key"
    envfile = SECRETS_DIR / "env" / f"{alias}.env"
    if not keyfile.exists():
        return {"alias": alias, "ok": False, "reason": f"{keyfile} missing"}
    key = keyfile.read_text().strip()
    if envfile.exists() and envfile.read_text().split("=", 1)[-1].strip() != key:
        return {"alias": alias, "ok": False, "reason": f"{envfile} disagrees with {keyfile}"}

    req = urllib.request.Request(
        f"{LITELLM}/key/info", headers={"Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            info = json.loads(resp.read()).get("info", {})
    except urllib.error.HTTPError as e:
        return {"alias": alias, "ok": False, "reason": f"gateway rejected the key: HTTP {e.code}"}
    except OSError as e:
        return {"alias": alias, "ok": False, "reason": f"gateway unreachable: {e}"}
    return {
        "alias": alias,
        "ok": True,
        "gateway_alias": info.get("key_alias"),
        "max_budget": info.get("max_budget"),
        "spend": info.get("spend"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("aliases", nargs="*")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    results = [check(x) for x in (a.aliases or services())]
    if a.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            if r["ok"]:
                print(f"OK   {r['alias']:<20} gateway alias {r['gateway_alias']} "
                      f"(spend ${r['spend']} / ${r['max_budget']})")
            else:
                print(f"FAIL {r['alias']:<20} {r['reason']}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
