#!/usr/bin/env python3
"""repo_sweep — periodic divergence detection across managed repos. wq#800.

Iterates every managed repo (LANE_REPO values + explicit extras), runs
repo_reconcile.py status, and records each non-IN_SYNC verdict as a keyed aq
row. A wq ticket is filed only by the aq escalation responder once the same
row has repeated (bin/aq_escalate.py, wq#965 shape; wq#1674).

wq#1674: the sweep used to `wq add` a titled ticket on every fire. wq add's dup
guard only sees OPEN rows, so once a worker closed "[repo-sweep] bin:
BEHIND_ONLY" the next sweep filed it again — 9 times in 9 days for bin, 6 for
foreman, 21 of 419 sampled closes fleet-wide (research/TICKET_CLOSURE_AUDIT.md
F5/P2). A divergence is an ALERT, not a task: it belongs in aq, where re-firing
is count++ on one row, and becomes a task only when it persists.

The keyed row is (source='repo-sweep', key='{repo}:{VERDICT}'), so:
  - re-firing the same verdict for the same repo is count++, never a new row;
  - a repo returning to IN_SYNC resolves its open rows here (the edge-clear),
    which is what scopes `count` to one incident and lets a LATER divergence
    open a NEW row and therefore earn a NEW ticket;
  - a repo whose verdict CHANGES resolves the old row and opens the new one.

Does NOT auto-apply any fix — SAFE_RESET repair on a live deployment tree
stays an explicit human/worker action.

Exit 0: all repos in sync.
Exit 1: at least one repo diverged (aq row(s) recorded).
Exit 2: internal error.
"""
import os
import re
import sqlite3
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
RECONCILE = os.path.join(HARNESS_DIR, "repo_reconcile.py")

# Import LANE_REPO from the orchestrator so the sweep covers exactly what
# the dispatcher considers managed.  Fall back to a static copy if the import
# path is broken (the sweep must not silently shrink its scope).
def _lane_repos():
    """Return the set of repo paths from LANE_REPO."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(HARNESS_DIR), "orchestrator"))
        from router import LANE_REPO, REPO_TAGS  # noqa: E402
        return set(LANE_REPO.values()) | set(REPO_TAGS.values())  # wq#4623
    except Exception:
        return set()


# Extra repos the ticket calls out that are not in LANE_REPO.
EXTRA_REPOS = [
    "/home/lumen/bin",
    # wq#1453-1456: the fleet inventory SoT repo (Gitea lumen/fleet-inventory) has no
    # lane of its own; tickets reach it through needs:repo(), which router.needs_repo_hint
    # only honours for a managed repo. Without this line the tag is dropped, the lane
    # repo (foreman) wins, and every to-be-created path under the repo is reported by
    # capability_lint as "does not exist".
    "/home/lumen/fleet/inventory",
    # wq#1540: minerva has no lane either, so a needs:repo(/home/lumen/minerva) ticket
    # was dispatched into a foreman worktree it cannot edit (ISSUE-LOG #477's class).
    "/home/lumen/minerva",
    # wq#2853 (ISSUE-LOG #881): the storage-audit fleet agent had no lane and, unlike
    # the two entries above, was never a git repo at all -- it was tracked only by
    # hand-copied *.bak snapshots, so a ticket targeting it dispatched into a foreman
    # worktree that could never satisfy a citation gate for it. git-initialized
    # (local main, no remote -- repo_reconcile's NO_UPSTREAM verdict, wq#913, is the
    # sanctioned shape for a local-only repo) so needs:repo() and cross-repo citations
    # both work going forward.
    "/home/lumen/agents/storage-audit",
]

TITLE_PREFIX = "[repo-sweep] "

# Matches the first line this module writes into an escalated ticket's detail (see
# ticket_detail() below): "Automated sweep detected {verdict} in {repo}."
_DETAIL_REPO_RE = re.compile(r"^Automated sweep detected \S+ in (\S+)\.")


def _simpill_clones():
    """wq#2242: the simpill lane's managed clones (harness/simpill_repos.json). They are a valid
    needs:repo(...) target but deliberately NOT swept for divergence: the watcher owns them."""
    try:
        sys.path.insert(0, HARNESS_DIR)
        import simpill_watch  # noqa: E402
        return simpill_watch.managed_clones()
    except Exception:
        return set()


def managed_repos():
    """The exact repo universe a sweep can file a ticket against."""
    return _lane_repos() | set(EXTRA_REPOS) | _simpill_clones()


def repo_hint(title, detail):
    """wq#1094: read back the exact repo a repo-sweep ticket targets.

    repo_sweep tickets carry the full absolute repo path in their own detail
    (written by ticket_detail() below) — the dispatcher's 'auto' lane otherwise
    has no single LANE_REPO entry since sweeps span arbitrary repos (wq#830).
    This is reading data this module itself wrote, not guessing: returns None
    (never a guess) unless the title marks a repo-sweep ticket AND the parsed
    path is still a member of the exact set sweep_repos() iterates, so a
    hand-edited or stale detail can't point dispatch at an arbitrary path.
    """
    if not title.startswith(TITLE_PREFIX):
        return None
    m = _DETAIL_REPO_RE.match(detail or "")
    if not m:
        return None
    repo = m.group(1)
    return repo if repo in managed_repos() else None

# The sweep talks to aq, never to wq (wq#1674). aq_escalate.py owns the wq side.
AQ_BIN = os.environ.get("REPO_SWEEP_AQ", "/home/lumen/bin/aq")
AQ_DB = os.environ.get("AQ_DB", "/home/lumen/agents/alert-queue/alerts.db")
AQ_SOURCE = "repo-sweep"

# Verdict names from repo_reconcile.py. Anything missing here is read as ERROR and files
# no ticket, so a new verdict has to arrive in the same wave as the exit code — wq#975 is
# the one that added 11, wq#1472 the one that added 12.
VERDICTS = {10: "SAFE_RESET", 11: "REDUNDANT_AHEAD", 12: "PR_PENDING",
            20: "CONTENT_DIVERGED", 30: "BEHIND_ONLY", 40: "NO_UPSTREAM"}


def aq_key(repo, verdict):
    """The keyed identity of one divergence. Repo paths carry no ':', so the
    verdict split is unambiguous."""
    return f"{repo}:{verdict}"


def ticket_title(repo, verdict):
    """Title the escalated wq ticket will carry. Kept here, next to repo_hint,
    so the TITLE_PREFIX the dispatcher matches on cannot drift from the writer."""
    return f"{TITLE_PREFIX}{os.path.basename(repo)}: {verdict}"


def ticket_detail(repo, verdict, output, reconcile=None):
    """Detail the escalated wq ticket will carry. Its FIRST line is the contract
    _DETAIL_REPO_RE / repo_hint read back (wq#1094) — keep it first."""
    reconcile = reconcile or RECONCILE
    return (f"Automated sweep detected {verdict} in {repo}.\n"
            f"Output: {output}\n\n"
            f"To inspect: python3 {reconcile} status --repo {repo}\n"
            f"To fix (if SAFE_RESET, REDUNDANT_AHEAD or PR_PENDING): "
            f"python3 {reconcile} reconcile --repo {repo} --apply")


def open_rows(repo, aq_db=None):
    """(id, key) of every open aq row this sweep owns for `repo`.

    Read-only sqlite, same shape governor/foreman_usaged.py uses: aq has no
    query-by-key subcommand, and reading the row id is the only way to resolve
    it. An unreadable/absent db is not fatal — the sweep still reports."""
    try:
        c = sqlite3.connect(aq_db or AQ_DB, timeout=10)
        rows = c.execute(
            "SELECT id, key FROM alerts WHERE source=? AND key LIKE ? AND status='open'",
            (AQ_SOURCE, f"{repo}:%")).fetchall()
        c.close()
    except sqlite3.Error:
        return []
    return [(r[0], r[1]) for r in rows]


def _aq(argv, aq_bin=None):
    """Run aq; never let the alert queue break the sweep. Returns (ok, output)."""
    aq_bin = aq_bin or AQ_BIN
    try:
        r = subprocess.run([aq_bin, *argv], capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def clear_stale_rows(repo, keep_key, note, aq_bin=None, aq_db=None):
    """Resolve every open row for `repo` except `keep_key` (None clears all).

    This is the edge-clear that scopes `count` to a single incident: without it
    a repo fixed today keeps yesterday's row open forever, a fresh divergence
    just increments it, and aq_escalate — which suppresses per aq_id — would
    never ticket again. Returns the number of rows resolved."""
    cleared = 0
    for rid, key in open_rows(repo, aq_db=aq_db):
        if key == keep_key:
            continue
        ok, _ = _aq(["resolve", str(rid), note], aq_bin=aq_bin)
        if ok:
            cleared += 1
    return cleared


def _detect_branch_and_remote(repo):
    """Return (branch, remote) for the repo's default integration branch.

    The sweep cares about the deployment branch (main or master), not whatever
    feature branch HEAD happens to be on. Checks main/master first, but only a
    candidate that ALSO exists as a local branch here — wq#913: pstack has
    origin/main (an upstream fork's default branch) but is locally checked out
    on `master`, and picking "main" from the remote alone sent repo_reconcile
    comparing origin/main against a local `main` ref that does not exist,
    which raised out of rev-list and read as ERROR to the sweep. Falls back to
    the HEAD branch's tracking remote, same as before.
    """
    try:
        for candidate in ("main", "master"):
            local = subprocess.run(
                ["git", "-C", repo, "rev-parse", "--verify", "--quiet", candidate],
                capture_output=True, text=True, timeout=10)
            if local.returncode != 0:
                continue
            p = subprocess.run(
                ["git", "-C", repo, "rev-parse", "--verify", f"origin/{candidate}"],
                capture_output=True, text=True, timeout=10)
            if p.returncode == 0:
                return candidate, "origin"
        # No local main/master with a matching origin branch — use HEAD branch's remote.
        branch = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10).stdout.strip()
        if not branch or branch == "HEAD":
            return "main", "origin"
        remote = subprocess.run(
            ["git", "-C", repo, "config", f"branch.{branch}.remote"],
            capture_output=True, text=True, timeout=10).stdout.strip()
        return branch, remote or "origin"
    except Exception:
        return "main", "origin"


def sweep_repos(repos=None, aq_bin=None, aq_db=None, reconcile=None, dry_run=False):
    """Run status on each repo, record non-zero verdicts as aq rows. Returns results."""
    if repos is None:
        repos = sorted(_lane_repos() | set(EXTRA_REPOS))
    if reconcile is None:
        reconcile = RECONCILE

    results = []
    for repo in repos:
        if not os.path.isdir(os.path.join(repo, ".git")):
            results.append({"repo": repo, "verdict": "NOT_A_REPO", "rc": 2,
                            "aq_row": False, "error": f"{repo} is not a git repo"})
            continue

        branch, remote = _detect_branch_and_remote(repo)
        p = subprocess.run(
            [sys.executable, reconcile, "status", "--repo", repo,
             "--branch", branch, "--remote", remote],
            capture_output=True, text=True, timeout=120)
        rc = p.returncode
        verdict = VERDICTS.get(rc, "IN_SYNC" if rc == 0 else "ERROR")
        entry = {"repo": repo, "verdict": verdict, "rc": rc,
                 "output": (p.stdout + p.stderr).strip(), "aq_row": False, "cleared": 0}

        if rc != 0 and verdict != "ERROR":
            key = aq_key(repo, verdict)
            entry["aq_key"] = key
            if dry_run:
                entry["aq_row"] = "dry_run"
            else:
                # Title/message are written in the exact shape the escalated wq
                # ticket needs, so aq_escalate never has to reconstruct (or drift
                # from) the format repo_hint reads back.
                ok, out = _aq(["add", AQ_SOURCE, key,
                               ticket_title(repo, verdict),
                               ticket_detail(repo, verdict, entry["output"], reconcile)],
                              aq_bin=aq_bin)
                entry["aq_row"] = bool(ok)
                if not ok:
                    entry["aq_error"] = out
                else:
                    entry["cleared"] = clear_stale_rows(
                        repo, key, f"verdict changed to {verdict} at sweep time",
                        aq_bin=aq_bin, aq_db=aq_db)
        elif rc == 2:
            entry["error"] = entry.get("output", "repo_reconcile error")
        elif rc == 0 and not dry_run:
            # Edge-clear: in sync means whatever we opened for this repo is over.
            entry["cleared"] = clear_stale_rows(
                repo, None, "repo back IN_SYNC at sweep time",
                aq_bin=aq_bin, aq_db=aq_db)

        results.append(entry)
    return results

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="report verdicts without touching the alert queue")
    ap.add_argument("--repo", action="append", default=None,
                    help="override: sweep only these repos (repeatable)")
    a = ap.parse_args(argv)

    results = sweep_repos(repos=a.repo, dry_run=a.dry_run)

    diverged = 0
    for r in results:
        status = r["verdict"]
        row = r["aq_row"]
        tag = ""
        if row is True:
            tag = " [aq row recorded]"
        elif row == "dry_run":
            tag = " [dry run — would record]"
        elif r.get("aq_error"):
            tag = f" [aq FAILED: {r['aq_error'][:120]}]"
        if r.get("cleared"):
            tag += f" [{r['cleared']} stale aq row(s) resolved]"
        print(f"{status} {r['repo']}{tag}")
        if r.get("output"):
            for line in r["output"].splitlines():
                print(f"  {line}")
        if status not in ("IN_SYNC",):
            diverged += 1

    return 1 if diverged else 0


if __name__ == "__main__":
    sys.exit(main())
