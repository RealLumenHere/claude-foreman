#!/usr/bin/env python3
"""simpill_watch — the simpill lane's producer (wq#2242, Frank 2026-09-20).

We watch every repo on Jay's account listed in simpill_repos.json. Per upstream event that
matters (push to the default branch, merged PR, a new issue or a review aimed at us) this files
ONE priority-0 ticket in lane `simpill`, carrying `needs repo(<managed clone>)` so the
dispatcher resolves the repo from the ticket (dispatcher/gates.py LaneRepo: the lane is
`multi_repo`). Each ticket's detail is the contract: evaluate, test, fix, test, fix, test, PR.

Dedupe: state (STATE) records every event key ever seen per repo. A key is written only after
`wq add` succeeded (or answered DUPLICATE), so a failed filing retries next poll and a repeat of
the same event files nothing. A repo seen for the first time is baselined — its current events
are recorded, none filed — so adding a repo never floods the queue with history.

A merged PR whose merge commit is the pushed head is ONE event, not two (the PR covers the push).

A new watched repo is one line in simpill_repos.json `repos`. `clone_root/<repo>` is a managed
clone (repo_sweep.managed_repos() lists it, which is what lets router.needs_repo_hint honour the
tag); the watcher clones it and fetches it every poll. `fork_owner`, when set, is wired as the
`fork` push remote. No credential is ever put in a remote URL: git uses its credential helper.

  simpill_watch.py                    poll every repo, sync clones, file tickets
  simpill_watch.py --dry-run          print what would be filed, write nothing
  simpill_watch.py --events F.json    use F's events instead of the GitHub API (synthetic proof);
                                      F is {repo: [{kind, ident, title, url, summary}, ...]}

Exit 0 clean, 1 if any repo errored, 2 on bad config.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.environ.get("SIMPILL_REPOS_CONFIG", os.path.join(HARNESS_DIR, "simpill_repos.json"))
STATE = os.environ.get("SIMPILL_STATE", os.path.join(
    os.path.dirname(HARNESS_DIR), "dispatcher", "runtime", "simpill_seen.json"))
WQ = os.environ.get("SIMPILL_WQ", "/home/lumen/bin/wq")
TOKEN_FILE = os.environ.get("SIMPILL_GITHUB_TOKEN_FILE", "/home/lumen/.secrets/github-frank.token")
LANE = "simpill"
PRIORITY = 0
SEEN_CAP = 500
PER_PAGE = 20
KINDS = ("push", "pr_merged", "issue", "review")

CONTRACT = """\
Frank's simpill lane (2026-09-20): we watch every project on Jay's account. When a change lands we
evaluate it, test, fix, test, fix, test, then open a PR to his repo, on the fly.

Upstream event: {kind} {url}
{summary}

Repo clone: {clone}  (origin = {upstream}; push remote `fork` = {fork})
Work in your worktree of that clone, branched from origin's default branch.

1. EVALUATE: read the event and the diff it brings (git log/diff against the previous head).
2. TEST: run the repo's own test suite (package.json scripts / Makefile / CI config).
3. FIX what the event broke or left broken; keep every change minimal and in the repo's style.
4. TEST again; repeat fix/test until green. Report anything you cannot make green.
5. PR: push the branch to the `fork` remote and open a DRAFT PR against {owner}/{repo} under the
   Lumen identity, following the contribution rules in the jay-watch agent dir (LUMEN-IDENTITY,
   JAY-ACTIVITY). Never push to origin. If there is nothing to fix, close the ticket with the
   evaluation as the notes instead of opening an empty PR.
Dedupe key: {key}
"""


def load_config(path=None):
    """Validated config dict; ValueError on anything malformed (a bad list must not poll)."""
    with open(path or CONFIG) as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise ValueError("config is not an object")
    for field in ("owner", "clone_root"):
        if not isinstance(cfg.get(field), str) or not cfg[field]:
            raise ValueError(f"config.{field} must be a non-empty string")
    repos = cfg.get("repos")
    if not isinstance(repos, list) or not all(isinstance(r, str) and _REPO_RE.fullmatch(r) for r in repos):
        raise ValueError("config.repos must be a list of plain repo names")
    if cfg.get("fork_owner") is not None and not isinstance(cfg["fork_owner"], str):
        raise ValueError("config.fork_owner must be a string or null")
    if not isinstance(cfg.get("us", []), list):
        raise ValueError("config.us must be a list of handles")
    return cfg


_REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def clone_path(cfg, repo):
    return os.path.join(cfg["clone_root"], repo)


def managed_clones(path=None):
    """Clone paths of every watched repo, for repo_sweep.managed_repos(). Empty on an unreadable
    config: the managed set must never break the dispatcher's own imports."""
    try:
        cfg = load_config(path)
    except (OSError, ValueError):
        return set()
    return {clone_path(cfg, r) for r in cfg["repos"]}


def upstream_url(cfg, repo):
    return f"https://github.com/{cfg['owner']}/{repo}.git"


def fork_url(cfg, repo):
    return f"https://github.com/{cfg['fork_owner']}/{repo}.git" if cfg.get("fork_owner") else None


# ---------------------------------------------------------------- clones

def _git(args, check=True):
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=300, check=check)


def sync_clone(cfg, repo, git=_git):
    """Clone if absent, else fetch; keep the `fork` remote pointing at our fork. The local branch
    is only fast-forwarded (never reset) so a worker's checkout is never clobbered."""
    path = clone_path(cfg, repo)
    if not os.path.isdir(os.path.join(path, ".git")):
        os.makedirs(cfg["clone_root"], exist_ok=True)
        git(["clone", upstream_url(cfg, repo), path])
    else:
        git(["-C", path, "fetch", "--prune", "origin"])
        git(["-C", path, "merge", "--ff-only", "@{u}"], check=False)
    fork = fork_url(cfg, repo)
    if fork:
        have = git(["-C", path, "remote"]).stdout.split()
        git(["-C", path, "remote", "set-url" if "fork" in have else "add", "fork", fork])
    return path


# ---------------------------------------------------------------- events

def _token():
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip() or None
    except OSError:
        return None


def github_api(path):
    """GET https://api.github.com<path> -> parsed JSON. The token stays in this frame only."""
    req = urllib.request.Request("https://api.github.com" + path, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "lumen-simpill-watch"})
    tok = _token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def event_key(repo, kind, ident):
    return f"{repo}:{kind}:{ident}"


def _event(cfg, repo, kind, ident, title, url, summary, covers=()):
    return {"repo": repo, "kind": kind, "id": str(ident), "key": event_key(repo, kind, ident),
            "title": " ".join((title or "").split())[:80], "url": url, "summary": summary,
            "covers": list(covers)}


def _aimed_at_us(cfg, *texts_and_logins):
    """No `us` handles configured means every issue counts (we cannot tell what is aimed at us)."""
    us = {u.lower() for u in cfg.get("us", [])}
    if not us:
        return True
    blob = " ".join(str(t) for t in texts_and_logins if t).lower()
    return any(f"@{u}" in blob or u in blob.split() for u in us)


def collect_events(cfg, repo, api=github_api):
    """Recent upstream events of one repo, newest first inside each kind."""
    slug = f"{cfg['owner']}/{repo}"
    meta = api(f"/repos/{slug}")
    branch = meta["default_branch"]
    events = []
    head = api(f"/repos/{slug}/commits/{branch}")
    prs = [p for p in api(f"/repos/{slug}/pulls?state=closed&sort=updated&direction=desc&per_page={PER_PAGE}")
           if p.get("merged_at")]
    for p in prs:
        sha = p.get("merge_commit_sha")
        events.append(_event(cfg, repo, "pr_merged", p["number"], p["title"], p["html_url"],
                             f"Merged PR #{p['number']} by {p['user']['login']}.",
                             covers=[event_key(repo, "push", sha)] if sha else ()))
    msg = head["commit"]["message"].splitlines()[0]
    events.append(_event(cfg, repo, "push", head["sha"], msg, head["html_url"],
                         f"{branch} is now {head['sha'][:12]}: {msg}"))
    for i in api(f"/repos/{slug}/issues?state=open&sort=created&direction=desc&per_page={PER_PAGE}"):
        if "pull_request" in i:
            continue
        if _aimed_at_us(cfg, i.get("body"), i.get("title"), *[a["login"] for a in i.get("assignees", [])]):
            events.append(_event(cfg, repo, "issue", i["number"], i["title"], i["html_url"],
                                 f"New issue #{i['number']} by {i['user']['login']}."))
    us = {u.lower() for u in cfg.get("us", [])}
    if us:
        for p in api(f"/repos/{slug}/pulls?state=open&per_page={PER_PAGE}"):
            if p["user"]["login"].lower() not in us:
                continue
            for rv in api(f"/repos/{slug}/pulls/{p['number']}/reviews"):
                if rv.get("state") in ("CHANGES_REQUESTED", "COMMENTED", "APPROVED"):
                    events.append(_event(cfg, repo, "review", rv["id"], f"review on PR #{p['number']}",
                                         rv.get("html_url") or p["html_url"],
                                         f"{rv['user']['login']} left a {rv['state']} review on our PR #{p['number']}."))
    return events


# ---------------------------------------------------------------- state and filing

def load_state(path=None):
    try:
        with open(path or STATE) as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state, path=None):
    path = path or STATE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def ticket_args(cfg, ev):
    """The `wq add` argv (after the binary) for one event."""
    repo = ev["repo"]
    detail = CONTRACT.format(kind=ev["kind"], url=ev["url"], summary=ev["summary"], clone=clone_path(cfg, repo),
                             upstream=upstream_url(cfg, repo), fork=fork_url(cfg, repo) or "(fork_owner unset: none yet)",
                             owner=cfg["owner"], repo=repo, key=ev["key"])
    short = ev["id"][:8] if ev["kind"] == "push" else f"#{ev['id']}"
    title = f"[simpill] {cfg['owner']}/{repo} {ev['kind']} {short}: {ev['title']}"
    return ["add", "--lane", LANE, "--pri", str(PRIORITY), "--title", title, "--detail", detail,
            "--needs", f"repo({clone_path(cfg, repo)})",
            "--no-probe", "upstream-event work item: no deterministic liveness check exists"]


def _wq(args):
    p = subprocess.run([WQ, *args], capture_output=True, text=True, timeout=60)
    return p.returncode, (p.stdout + p.stderr)


def file_event(cfg, ev, wq=_wq):
    """True when the event now has a ticket (filed here or already open); False to retry later."""
    rc, out = wq(ticket_args(cfg, ev))
    return rc == 0 or "DUPLICATE" in out


def _remember(seen, keys):
    for k in keys:
        if k not in seen:
            seen.append(k)
    del seen[:-SEEN_CAP]


def process_repo(cfg, repo, events, state, wq=_wq, dry_run=False):
    """Dedupe `events` against `state[repo]`, file the new ones. Returns the list of keys filed."""
    rs = state.setdefault(repo, {"baselined": False, "seen": []})
    seen = rs["seen"]
    if not rs["baselined"]:
        if not dry_run:
            _remember(seen, [k for e in events for k in [e["key"], *e["covers"]]])
            rs["baselined"] = True
        return []
    new = [e for e in events if e["key"] not in seen]
    covered = {k for e in new for k in e["covers"]}
    filed = []
    for ev in new:
        if ev["key"] in covered:
            if not dry_run:
                _remember(seen, [ev["key"]])
            continue
        if dry_run or file_event(cfg, ev, wq):
            filed.append(ev["key"])
            if not dry_run:
                _remember(seen, [ev["key"], *ev["covers"]])
    return filed


def run_once(cfg, state, api=github_api, git=_git, wq=_wq, events_override=None, dry_run=False, log=print):
    """One poll of every watched repo. Returns (filed keys, error count)."""
    filed, errors = [], 0
    for repo in cfg["repos"]:
        try:
            if not dry_run:
                sync_clone(cfg, repo, git)
            events = events_override.get(repo, []) if events_override is not None else collect_events(cfg, repo, api)
            filed += process_repo(cfg, repo, events, state, wq, dry_run)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, urllib.error.URLError) as e:
            errors += 1
            log(f"simpill_watch: {repo}: {type(e).__name__}: {str(e).splitlines()[0][:160] if str(e) else ''}")
    return filed, errors


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--events", help="JSON {repo: [event,...]} used instead of the GitHub API")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
    except (OSError, ValueError) as e:
        print(f"simpill_watch: bad config {a.config}: {e}", file=sys.stderr)
        return 2
    override = None
    if a.events:
        with open(a.events) as f:
            override = {r: [_event(cfg, r, **e) for e in evs] for r, evs in json.load(f).items()}
    state = load_state()
    filed, errors = run_once(cfg, state, git=_git, wq=_wq, events_override=override, dry_run=a.dry_run)
    if not a.dry_run:
        save_state(state)
    for key in filed:
        print(f"{'would file' if a.dry_run else 'filed'}: {key}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
