#!/usr/bin/env python3
"""push_protection.py — audit/enable GitHub secret scanning + push protection
on every fleet repo (wq#2276, token-engine T7, design §6 "provider-side push
protection").

Inventory comes from /home/lumen/infra/github/agent-scope.conf (the single
"may an agent touch this repo" registry, wq#546) plus a live enumeration of
the RealLumenHere account. Repos under the `lumen/` owner live on the Anubis
Gitea, which has no secret-scanning feature — they are reported as
forge-unsupported rather than silently skipped.

  push_protection.py scan          # read-only: report per-repo state
  push_protection.py scan --fix    # also PATCH push protection on where the
                                   # token has admin and the scope class allows
  push_protection.py scan --json   # machine-readable

Token resolution follows lumen-github-identity.sh: per-repo file from
identity-map.conf, else the per-owner default under ~/.secrets. Tokens are
never printed; output is repo slug + state only (instinct #4).

NEVER-class repos (agent-scope NEVER lines) are never mutated — their state is
read (a GET is not a write) and any needed change is reported as owner-queued.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCOPE_CONF = Path("/home/lumen/infra/github/agent-scope.conf")
IDENTITY_MAP = Path("/home/lumen/infra/github/identity-map.conf")
SECRETS = Path("/home/lumen/.secrets")
OWNER_DEFAULT_TOKEN = {
    "frankstupak": SECRETS / "github-frank.token",
    "RealLumenHere": SECRETS / "github-lumen-classic.token",
}
API = "https://api.github.com"
UA = "lumen-push-protection-wq2276"
GITEA_OWNER = "lumen"  # agent-scope `lumen/*` slugs live on the Anubis Gitea


def load_scope():
    """agent-scope.conf -> {slug: verb} (ALLOW | NEVER | PUBLIC)."""
    repos = {}
    for raw in SCOPE_CONF.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        m = re.match(r"(ALLOW|NEVER|PUBLIC)\s+(\S+/\S+)$", line)
        if m:
            repos[m.group(2)] = m.group(1)
    return repos


def identity_map():
    """identity-map.conf -> {slug: token-file Path}."""
    out = {}
    if not IDENTITY_MAP.exists():
        return out
    for raw in IDENTITY_MAP.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if "=" in line:
            slug, path = (s.strip() for s in line.split("=", 1))
            out[slug] = Path(path)
    return out


def tokens_for(slug, imap):
    """-> [(token, source-path-str), ...] best-first. Never prints a token.

    Order: identity-map entry, owner default, then every other owner default —
    the identity-map token is the right *author* identity but may lack admin
    (RealLumenHere is not admin on frankstupak/lumen-plays-pokemon), and admin
    is what a settings PATCH needs.
    """
    owner = slug.split("/", 1)[0]
    files = []
    if slug in imap:
        files.append(imap[slug])
    if owner in OWNER_DEFAULT_TOKEN:
        files.append(OWNER_DEFAULT_TOKEN[owner])
    files += [f for f in OWNER_DEFAULT_TOKEN.values() if f not in files]
    out = []
    for f in files:
        try:
            tok = f.read_text().strip()
        except OSError:
            continue
        if tok:
            out.append((tok, str(f)))
    return out


def gh(method, path, token, body=None):
    """-> (status, parsed-json-or-None). Raises nothing; errors come back as status."""
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": UA,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"null")
        except Exception:
            detail = None
        return e.code, detail
    except urllib.error.URLError as e:
        return 0, {"message": str(e.reason)}


def list_owner_repos(owner, token):
    """All non-archived repos owned by `owner`, private included when the
    token IS that owner (/user/repos sees private; /users/{owner}/repos
    only ever returns public)."""
    slugs, page = [], 1
    st, me = gh("GET", "/user", token)
    base = ("/user/repos?affiliation=owner" if st == 200
            and (me or {}).get("login") == owner else f"/users/{owner}/repos?")
    while True:
        st, data = gh("GET", f"{base}&per_page=100&page={page}", token)
        if st != 200 or not data:
            break
        slugs += [r["full_name"] for r in data
                  if not r.get("archived") and r["full_name"].startswith(owner + "/")]
        if len(data) < 100:
            break
        page += 1
    return slugs


def repo_state(slug, token):
    """-> dict with private/admin/secret_scanning/push_protection or error."""
    st, data = gh("GET", f"/repos/{slug}", token)
    if st != 200:
        return {"error": f"GET {st}: {(data or {}).get('message', '?')}"}
    sa = data.get("security_and_analysis") or {}

    def status(key):
        return (sa.get(key) or {}).get("status", "unknown")

    return {
        "private": data.get("private"),
        "admin": (data.get("permissions") or {}).get("admin", False),
        "secret_scanning": status("secret_scanning"),
        "push_protection": status("secret_scanning_push_protection"),
    }


def enable_push_protection(slug, token, private):
    """PATCH push protection (and secret scanning too on private repos) on."""
    sa = {"secret_scanning_push_protection": {"status": "enabled"}}
    if private:
        # public repos always have secret scanning; PATCHing it there 422s
        sa["secret_scanning"] = {"status": "enabled"}
    st, data = gh("PATCH", f"/repos/{slug}", token, {"security_and_analysis": sa})
    if st == 200:
        return "enabled"
    return f"PATCH {st}: {(data or {}).get('message', '?')}"


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--fix", action="store_true")
    scan.add_argument("--json", action="store_true")
    args = ap.parse_args()

    scope = load_scope()
    imap = identity_map()
    rows = []

    # live RealLumenHere enumeration — design §7 rank 2 says "all RealLumenHere
    # repos", not just the ones in agent-scope
    rll = tokens_for("RealLumenHere/x", imap)
    if rll:
        for slug in list_owner_repos("RealLumenHere", rll[0][0]):
            scope.setdefault(slug, "ALLOW")

    for slug, verb in sorted(scope.items()):
        owner = slug.split("/", 1)[0]
        if owner == GITEA_OWNER:
            rows.append({
                "repo": slug, "forge": "gitea", "scope": verb,
                "state": "forge-unsupported",
                "note": "Gitea has no secret scanning/push protection; "
                        "pre-receive hook is the follow-up",
            })
            continue
        candidates = tokens_for(slug, imap)
        if not candidates:
            rows.append({"repo": slug, "forge": "github", "scope": verb,
                         "state": "no-token",
                         "note": "no readable token file for " + owner})
            continue
        token, st = None, None
        for tok, _src in candidates:
            s = repo_state(slug, tok)
            if token is None or ("error" not in s and s.get("admin")):
                token, st = tok, s
            if "error" not in s and s.get("admin"):
                break
        row = {"repo": slug, "forge": "github", "scope": verb, **st}
        if "error" not in st:
            needs = st["push_protection"] != "enabled" or (
                st["private"] and st["secret_scanning"] != "enabled")
            if not needs:
                row["state"] = "protected"
            elif verb == "NEVER":
                row["state"] = "owner-queued"
                row["note"] = "NEVER-class repo: agents may not write; owner must enable"
            elif not st["admin"]:
                row["state"] = "owner-queued"
                row["note"] = "token lacks admin on this repo"
            elif args.fix:
                result = enable_push_protection(slug, token, st["private"])
                if result == "enabled":
                    row["state"] = "protected"
                    row["note"] = "enabled by this run"
                else:
                    row["state"] = "owner-queued"
                    row["note"] = result
            else:
                row["state"] = "needs-fix"
        else:
            row["state"] = "error"
        rows.append(row)

    if args.json:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            print("%-45s %-7s %-6s %-13s ss=%s pp=%s %s" % (
                r["repo"], r["forge"], r["scope"], r["state"],
                r.get("secret_scanning", "-"), r.get("push_protection", "-"),
                r.get("note", r.get("error", ""))))
    bad = [r for r in rows if r["state"] in ("error", "no-token")]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
