#!/usr/bin/env python3
"""pr_recon.py — read-only reconciliation of open PRs against work-queue ticket state.

wq#899. The audit found ~40 open PRs across the Lumen Gitea repos whose tickets are
already `done`. "Done" was closing on a PR *URL*, never on the PR having landed, so a
closed ticket could point at code that lives only on a branch — or, worse, only in a
working tree. This script is the instrument that finds them; the fix that stops them
being created is the merge-state check in `wq done` (wq#759 gate extension).

For every open PR it answers three independent questions and prints the join:
  1. forge   — is the PR open, and does Gitea think it merges cleanly?
  2. git     — is the PR head reachable from origin/main already (cherry-picked / stale)?
  3. queue   — what status does the wq ticket referenced by the PR carry?

Never mutates anything: GET requests and `git merge-base --is-ancestor` only. The Gitea
token is loaded the same way lumen-pr loads it and is never printed.

Usage:  pr_recon.py [--json] [--repo SLUG]
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request

FORGE = os.environ.get("GITEA_URL", "http://100.121.17.5:3000") + "/api/v1"
DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")
TOKEN_FILE = os.environ.get("GITEA_TOKEN_FILE", "/home/lumen/.secrets/gitea-lumen.token")

# slug -> local clone used for reachability. A slug with no clone still gets forge+queue
# answers; only the `git` column degrades to "no-clone", which is stated, never guessed.
REPOS = {
    "lumen/claude-foreman": "/home/lumen/foreman",
    "lumen/lumen-bin": "/home/lumen/bin",
    "lumen/minerva": "/home/lumen/minerva",
    "lumen/mission-control": "/home/lumen/mission-control",
    "lumen/questfree": "/home/lumen/questfree",
    "lumen/lumen-messenger": "/home/lumen/messenger",
}

WQ_RE = re.compile(r"wq#(\d+)")


def load_token():
    """Same search order as lumen-pr. Returned, never logged."""
    try:
        with open(TOKEN_FILE) as fh:
            tok = fh.read().strip()
            if tok:
                return tok
    except OSError:
        pass
    try:
        with open("/home/lumen/agent/.env") as fh:
            for line in fh:
                if line.startswith("GITEA_TOKEN="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    sys.exit(f"no Gitea token: looked in {TOKEN_FILE} then /home/lumen/agent/.env")


def api(token, path):
    req = urllib.request.Request(
        FORGE + path,
        headers={"Authorization": f"token {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"_error": f"{e.code} {e.reason}"}


def git(clone, *args):
    p = subprocess.run(["git", "-C", clone, *args], capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def reachable(clone, sha):
    """Is `sha` already an ancestor of origin/main? Distinguishes 'unmerged' from
    'landed by another route' (squash/cherry-pick leaves the head sha unreachable, so a
    False here is a question, not a verdict — the patch-id check answers it)."""
    if clone is None:
        return "no-clone"
    if git(clone, "cat-file", "-e", sha + "^{commit}")[0] != 0:
        return "sha-absent"
    return "yes" if git(clone, "merge-base", "--is-ancestor", sha, "origin/main")[0] == 0 else "no"


def cherry_state(clone, base, sha):
    """`git cherry` compares patch-ids, so it sees a squash-merged or cherry-picked branch
    that reachability misses. '-' means every commit on the branch already exists on main
    in some form: the PR is a stale reference, not lost work."""
    if clone is None:
        return "no-clone"
    rc, out = git(clone, "cherry", base, sha)
    if rc != 0 or not out:
        return "empty"
    marks = {line.split()[0] for line in out.splitlines() if line.strip()}
    if marks == {"-"}:
        return "all-upstream"
    return "has-new" if "+" in marks else "mixed"


AUDIT_TAG = "wq#899"


def has_audit_note(token, slug, number):
    """Has a human-readable reconciliation note already been left on this PR?

    wq#899's acceptance criterion is 'no open PR on a done ticket WITHOUT an explicit
    stale-ref note', so the note is part of the pass condition and has to be checkable, not
    remembered. Reviews come first because the fleet token can post those; plain issue
    comments are checked too, for notes left by a human or a differently-scoped token."""
    for path in (f"/repos/{slug}/pulls/{number}/reviews",
                 f"/repos/{slug}/issues/{number}/comments"):
        items = api(token, path)
        if isinstance(items, list) and any(AUDIT_TAG in (i.get("body") or "") for i in items):
            return True
    return False


def merge_probe(clone, base, sha):
    """Would this merge cleanly into base *right now*? `merge-tree --write-tree` does a real
    three-way merge in memory and exits non-zero on conflict, without touching the worktree.
    Answers the question that decides land-vs-reopen, so it is measured, not guessed."""
    if clone is None:
        return "no-clone"
    rc, out = git(clone, "merge-tree", "--write-tree", "--name-only", f"origin/{base}", sha)
    if rc == 0:
        return "clean"
    if rc == 1:
        files = [l for l in out.splitlines()[1:] if l.strip()]
        return "conflict:" + ",".join(files[:4]) if files else "conflict"
    return "unmergeable"


def diffstat(clone, base, sha):
    if clone is None:
        return "no-clone"
    rc, out = git(clone, "diff", "--shortstat", f"origin/{base}...{sha}")
    return out.replace(" changed", "").replace(" insertions(+)", "+").replace(
        " insertion(+)", "+").replace(" deletions(-)", "-").replace(" deletion(-)", "-").strip() or "empty"


def ticket_rows(ids):
    if not ids:
        return {}
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = "SELECT id,status,title,notes FROM tasks WHERE id IN (%s)" % ",".join("?" * len(ids))
    rows = {r[0]: {"status": r[1], "title": r[2], "notes": r[3] or ""} for r in c.execute(q, list(ids))}
    c.close()
    return rows


def scan(slugs):
    token = load_token()
    out = []
    for slug in slugs:
        clone = REPOS.get(slug)
        if clone:
            git(clone, "fetch", "--quiet", "origin")
        pulls = api(token, f"/repos/{slug}/pulls?state=open&limit=100")
        if isinstance(pulls, dict):
            out.append({"repo": slug, "error": pulls.get("_error", "unexpected response")})
            continue
        for pr in pulls:
            head = (pr.get("head") or {})
            base = (pr.get("base") or {})
            sha = head.get("sha") or ""
            body = pr.get("body") or ""
            refs = sorted({int(n) for n in WQ_RE.findall(pr.get("title", "") + " " + body + " " + (head.get("ref") or ""))})
            out.append({
                "repo": slug,
                "number": pr.get("number"),
                "title": pr.get("title", ""),
                "head_ref": head.get("ref"),
                "head_sha": sha[:12],
                "base_ref": base.get("ref"),
                "created_at": pr.get("created_at"),
                "updated_at": pr.get("updated_at"),
                "mergeable": pr.get("mergeable"),
                "wq_refs": refs,
                "reachable": reachable(clone, sha) if sha else "no-sha",
                "cherry": cherry_state(clone, f"origin/{base.get('ref') or 'main'}", sha) if sha else "no-sha",
                "noted": has_audit_note(token, slug, pr.get("number")),
                "merge": merge_probe(clone, base.get("ref") or "main", sha) if sha else "no-sha",
                "diffstat": diffstat(clone, base.get("ref") or "main", sha) if sha else "no-sha",
            })
    tickets = ticket_rows({n for row in out for n in row.get("wq_refs", [])})
    for row in out:
        row["tickets"] = {n: tickets.get(n, {"status": "MISSING"}) for n in row.get("wq_refs", [])}
        row["cited"] = cited_by(row)
    return out


PR_NUM_RE = re.compile(r"/pulls?/(\d+)|(?:PR\s*)#(\d+)")


def cited_by(row):
    """Do the referenced tickets point back at THIS pr, or at a different one?

    The audit's dominant pattern turned out to be losing duplicate arms: two agents work the
    same ticket, one PR merges and gets cited in the ticket notes, the other stays open
    forever. 'cited=other:34' means the ticket's own evidence names a different PR — the open
    one is a stale reference, and closing it loses nothing."""
    mine, others = str(row.get("number")), set()
    for t in row["tickets"].values():
        for a, b in PR_NUM_RE.findall(t.get("notes", "")):
            n = a or b
            if n == mine:
                return "self"
            others.add(n)
    return ("other:" + ",".join(sorted(others)[:3])) if others else "none"


def verdict(row):
    """The recommendation this join implies. Advisory — a human or the closing agent still
    decides — but it makes the default action explicit instead of leaving 40 judgment calls
    to fatigue."""
    if row.get("error"):
        return "ERROR"
    statuses = {t["status"] for t in row["tickets"].values()}
    done = statuses and statuses <= {"done", "rejected"}
    if row["reachable"] == "yes" or row["cherry"] == "all-upstream":
        return "CLOSE-STALE"        # code is on main; the PR is a dangling reference
    if done and row.get("noted"):
        return "RECONCILED"         # done ticket, open PR, but the disposition is written on the PR
    if done:
        return "LAND-OR-REOPEN"     # ticket closed but the code is not on main — the bug wq#899 is about
    return "OPEN-WORK"              # ticket still in flight; PR is legitimately open


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repo", action="append", help="restrict to this slug (repeatable)")
    ap.add_argument("--gate", action="store_true",
                    help="wq#899 acceptance: exit 1 if any open PR sits on a done ticket with no note")
    a = ap.parse_args()
    rows = scan(a.repo or list(REPOS))
    for row in rows:
        row["verdict"] = verdict(row)
    if a.gate:
        bad = [r for r in rows if r.get("verdict") == "LAND-OR-REOPEN"]
        for r in bad:
            print(f"UNRECONCILED {r['repo']}#{r['number']} — ticket done, PR open, no wq#899 note: "
                  f"{r['title'][:60]}")
        print(f"{len(rows) - len(bad)}/{len(rows)} open PRs reconciled")
        sys.exit(1 if bad else 0)
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    for row in rows:
        if row.get("error"):
            print(f"{row['repo']}: ERROR {row['error']}")
            continue
        tick = " ".join(f"wq#{n}={t['status']}" for n, t in sorted(row["tickets"].items())) or "no-ref"
        print(f"{row['verdict']:<15} {row['repo'].split('/')[1]}#{row['number']:<4} "
              f"{row['merge'][:22]:<23} {row['diffstat'][:20]:<21} {tick:<26} {row['title'][:48]}")


if __name__ == "__main__":
    main()
