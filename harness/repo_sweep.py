#!/usr/bin/env python3
"""repo_sweep — periodic divergence detection across managed repos. wq#800.

Iterates every managed repo (LANE_REPO values + explicit extras), runs
repo_reconcile.py status, and files a wq ticket for each that is not IN_SYNC.
Idempotent: wq add rejects duplicate titles, so re-running the sweep while a
ticket is still open is a harmless no-op for that repo.

Does NOT auto-apply any fix — SAFE_RESET repair on a live deployment tree
stays an explicit human/worker action.

Exit 0: all repos in sync.
Exit 1: at least one repo diverged (ticket(s) filed or already open).
Exit 2: internal error.
"""
import os
import re
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
        from router import LANE_REPO  # noqa: E402
        return set(LANE_REPO.values())
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
]

TITLE_PREFIX = "[repo-sweep] "

# Matches the first line this module writes into a filed ticket's detail (see
# sweep_repos() below): "Automated sweep detected {verdict} in {repo}."
_DETAIL_REPO_RE = re.compile(r"^Automated sweep detected \S+ in (\S+)\.")


def managed_repos():
    """The exact repo universe a sweep can file a ticket against."""
    return _lane_repos() | set(EXTRA_REPOS)


def repo_hint(title, detail):
    """wq#1094: read back the exact repo a repo-sweep ticket targets.

    repo_sweep tickets carry the full absolute repo path in their own detail
    (written by sweep_repos() below) — the dispatcher's 'auto' lane otherwise
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

WQ_BIN = os.environ.get("REPO_SWEEP_WQ", "/home/lumen/bin/wq")

# Verdict names from repo_reconcile.py. Anything missing here is read as ERROR and files
# no ticket, so a new verdict has to arrive in the same wave as the exit code — wq#975 is
# the one that added 11, wq#1472 the one that added 12.
VERDICTS = {10: "SAFE_RESET", 11: "REDUNDANT_AHEAD", 12: "PR_PENDING",
            20: "CONTENT_DIVERGED", 30: "BEHIND_ONLY"}


def _detect_branch_and_remote(repo):
    """Return (branch, remote) for the repo's default integration branch.

    The sweep cares about the deployment branch (main or master), not whatever
    feature branch HEAD happens to be on.  Checks origin/main first, then
    origin/master, then falls back to the HEAD branch's tracking remote.
    """
    try:
        # Prefer the canonical deployment branch name.
        for candidate in ("main", "master"):
            p = subprocess.run(
                ["git", "-C", repo, "rev-parse", "--verify", f"origin/{candidate}"],
                capture_output=True, text=True, timeout=10)
            if p.returncode == 0:
                return candidate, "origin"
        # No origin/main or origin/master — try HEAD branch's remote.
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


def sweep_repos(repos=None, wq_bin=None, reconcile=None, dry_run=False):
    """Run status on each repo, file tickets for non-zero. Returns list of results."""
    if repos is None:
        repos = sorted(_lane_repos() | set(EXTRA_REPOS))
    if wq_bin is None:
        wq_bin = WQ_BIN
    if reconcile is None:
        reconcile = RECONCILE

    results = []
    for repo in repos:
        if not os.path.isdir(os.path.join(repo, ".git")):
            results.append({"repo": repo, "verdict": "NOT_A_REPO", "rc": 2,
                            "ticket_filed": False, "error": f"{repo} is not a git repo"})
            continue

        branch, remote = _detect_branch_and_remote(repo)
        p = subprocess.run(
            [sys.executable, reconcile, "status", "--repo", repo,
             "--branch", branch, "--remote", remote],
            capture_output=True, text=True, timeout=120)
        rc = p.returncode
        verdict = VERDICTS.get(rc, "IN_SYNC" if rc == 0 else "ERROR")
        entry = {"repo": repo, "verdict": verdict, "rc": rc,
                 "output": (p.stdout + p.stderr).strip(), "ticket_filed": False}

        if rc != 0 and verdict != "ERROR":
            short = os.path.basename(repo)
            title = f"[repo-sweep] {short}: {verdict}"
            detail = (f"Automated sweep detected {verdict} in {repo}.\n"
                      f"Output: {entry['output']}\n\n"
                      f"To inspect: python3 {reconcile} status --repo {repo}\n"
                      f"To fix (if SAFE_RESET, REDUNDANT_AHEAD or PR_PENDING): "
                      f"python3 {reconcile} reconcile --repo {repo} --apply")
            if dry_run:
                entry["ticket_filed"] = "dry_run"
            else:
                t = subprocess.run(
                    [wq_bin, "add", "--lane", "auto", "--pri", "1",
                     "--title", title, "--detail", detail],
                    capture_output=True, text=True, timeout=30)
                if t.returncode == 0:
                    entry["ticket_filed"] = True
                elif "DUPLICATE" in t.stdout or "DUPLICATE" in t.stderr:
                    entry["ticket_filed"] = "duplicate"
                else:
                    entry["ticket_filed"] = False
                    entry["wq_error"] = (t.stdout + t.stderr).strip()
        elif rc == 2:
            entry["error"] = entry.get("output", "repo_reconcile error")

        results.append(entry)
    return results

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="report verdicts without filing tickets")
    ap.add_argument("--repo", action="append", default=None,
                    help="override: sweep only these repos (repeatable)")
    a = ap.parse_args(argv)

    results = sweep_repos(repos=a.repo, dry_run=a.dry_run)

    diverged = 0
    for r in results:
        status = r["verdict"]
        filed = r["ticket_filed"]
        tag = ""
        if filed is True:
            tag = " [ticket filed]"
        elif filed == "duplicate":
            tag = " [ticket already open]"
        elif filed == "dry_run":
            tag = " [dry run — would file]"
        print(f"{status} {r['repo']}{tag}")
        if r.get("output"):
            for line in r["output"].splitlines():
                print(f"  {line}")
        if status not in ("IN_SYNC",):
            diverged += 1

    return 1 if diverged else 0


if __name__ == "__main__":
    sys.exit(main())
