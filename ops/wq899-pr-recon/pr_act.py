#!/usr/bin/env python3
"""pr_act.py — the write half of the wq#899 reconciliation.

pr_recon.py decides; this executes. Two verbs, both deliberately narrow:

  state <slug> <n>...        read merged/open state of specific PRs (read-only)
  close <slug> <n> --note S  post S as a comment, then close the PR

`close` always comments first and only closes if the comment posted, so a closed PR can
never end up without the stale-ref note that wq#899's acceptance criterion requires. It
refuses to close a PR that is already merged or already closed, which makes re-running the
whole reconciliation safe.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from pr_recon import FORGE, load_token  # noqa: E402


def call(token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(FORGE + path, data=data, method=method, headers={
        "Authorization": f"token {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"_error": f"{e.code} {e.reason}: {e.read()[:200].decode(errors='replace')}"}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("state"); s.add_argument("slug"); s.add_argument("numbers", nargs="+")
    for verb in ("close", "comment"):
        v = sub.add_parser(verb); v.add_argument("slug"); v.add_argument("number")
        v.add_argument("--note", required=True)
    a = ap.parse_args()
    token = load_token()

    if a.cmd == "state":
        for n in a.numbers:
            pr = call(token, "GET", f"/repos/{a.slug}/pulls/{n}")
            if "_error" in pr:
                print(f"#{n}: {pr['_error']}")
                continue
            print(f"#{n}: state={pr.get('state')} merged={pr.get('merged')} "
                  f"merge_sha={(pr.get('merge_commit_sha') or '')[:10]} {pr.get('title', '')[:50]}")
        return

    pr = call(token, "GET", f"/repos/{a.slug}/pulls/{a.number}")
    if "_error" in pr:
        sys.exit(f"cannot read {a.slug}#{a.number}: {pr['_error']}")
    if pr.get("merged") or pr.get("state") != "open":
        print(f"skip {a.slug}#{a.number}: already {'merged' if pr.get('merged') else pr.get('state')}")
        return
    # A review with event=COMMENT is the only way this token can leave a note: the fleet's
    # Gitea token carries write:repository but not write:issue, and plain PR comments go
    # through the issues API. Falls back to that API anyway, for a token scoped the other way.
    r = call(token, "POST", f"/repos/{a.slug}/pulls/{a.number}/reviews",
             {"body": a.note, "event": "COMMENT"})
    if "_error" in r:
        r = call(token, "POST", f"/repos/{a.slug}/issues/{a.number}/comments", {"body": a.note})
    if "_error" in r:
        sys.exit(f"comment failed on {a.slug}#{a.number} — NOT closing: {r['_error']}")
    if a.cmd == "comment":
        print(f"commented on {a.slug}#{a.number} (left open on purpose)")
        return
    r = call(token, "PATCH", f"/repos/{a.slug}/pulls/{a.number}", {"state": "closed"})
    if "_error" in r:
        sys.exit(f"close failed on {a.slug}#{a.number} (note posted): {r['_error']}")
    print(f"closed {a.slug}#{a.number} with stale-ref note")


if __name__ == "__main__":
    main()
