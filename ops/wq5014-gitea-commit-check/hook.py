#!/usr/bin/env python3
"""wq#5014: Gitea pre-receive commit policy (commit-check), FAIL-CLOSED.

Reads `<old> <new> <ref>` lines on stdin (git pre-receive). For every branch update, runs
commit-check against each commit the push introduces (`git rev-list <new> --not --all`) with
the fleet policy in /etc/lumen-commit-check/cchk.toml.

Exit status of commit-check per commit: 0 pass, 1 policy failure, 2 could not run (bad config,
git unreadable). 1 AND 2 both refuse the push, and so does any error of this hook itself
(timeout, missing venv, exception): a policy gate that waves pushes through when it breaks is
not a gate. That is the opposite of the wq#2286 secret scan, which fails open.

Break glass (root on Anubis only): touch /etc/lumen-commit-check/DISABLED. The hook then
accepts every push and logs one syslog line per push (tag lumen-commit-check). Remove the file
to re-arm.

Skipped: ref deletions, tags, refs outside refs/heads/ (Gitea's own refs/pull/* bookkeeping).
"""
import os
import subprocess
import sys
import syslog
import time

LIB = os.environ.get("LUMEN_CCHK_LIB", "/usr/local/lib/lumen-commit-check")
ETC = os.environ.get("LUMEN_CCHK_ETC", "/etc/lumen-commit-check")
SITE = os.path.join(LIB, "site")  # the pinned commit-check wheel, unzipped (no venv, no deps)
PY = os.environ.get("LUMEN_CCHK_PYTHON", "/usr/bin/python3")
BOOT = ("import sys; sys.path.insert(0, sys.argv.pop(1)); "
        "from commit_check.main import main; sys.exit(main())")
CONFIG = os.path.join(ETC, "cchk.toml")
DISABLED = os.path.join(ETC, "DISABLED")
# Repos that are not code (data syncs). One `owner/name` per line, '#' comments. Exempt repos
# accept every push; the list itself is the on-the-record decision.
EXEMPT = os.path.join(ETC, "exempt-repos")
ZERO = "0" * 40
PER_COMMIT_TIMEOUT = 20
BUDGET_S = float(os.environ.get("LUMEN_CCHK_BUDGET", "120"))
MAX_REPORT = 10


def log(msg):
    try:
        syslog.openlog("lumen-commit-check")
        syslog.syslog(syslog.LOG_WARNING, msg)
    except Exception:
        pass


def refuse(lines):
    for ln in lines:
        print(f"lumen-commit-check: {ln}", file=sys.stderr)
    print("lumen-commit-check: push REFUSED (wq#5014 commit policy). Fix the message with "
          "`git commit --amend` / `git rebase -i`, or land through `lumen-pr`, which writes "
          "compliant messages. Policy: house Conventional Commits, subject <= 80 chars, AI "
          "help disclosed as `Assisted-by:`, never an AI `Co-authored-by:`.", file=sys.stderr)
    sys.exit(1)


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=30)


def new_commits(new):
    r = git("rev-list", "--reverse", new, "--not", "--all")
    if r.returncode != 0:
        raise RuntimeError(f"git rev-list failed: {r.stderr.strip()[:200]}")
    return [s for s in r.stdout.split() if s]


def check(sha):
    """(rc, output) from commit-check on one commit."""
    r = subprocess.run([PY, "-I", "-c", BOOT, SITE, "-c", CONFIG, "--rev", sha, "--message",
                        "--no-banner", "--compact"], capture_output=True, text=True,
                       timeout=PER_COMMIT_TIMEOUT)
    return r.returncode, (r.stdout + r.stderr).strip()


def repo_slug():
    """owner/name from Gitea's hook env, else from the bare repo path (.../owner/name.git)."""
    u, n = os.environ.get("GITEA_REPO_USER_NAME"), os.environ.get("GITEA_REPO_NAME")
    if u and n:
        return f"{u}/{n}".lower()
    d = os.path.realpath(os.environ.get("GIT_DIR") or os.getcwd())
    return f"{os.path.basename(os.path.dirname(d))}/{os.path.basename(d)[:-4]}".lower() \
        if d.endswith(".git") else ""


def exempt(slug):
    try:
        with open(EXEMPT) as f:
            return slug in {ln.split("#")[0].strip().lower() for ln in f} - {""}
    except FileNotFoundError:
        return False


def main():
    updates = [ln.split() for ln in sys.stdin.read().splitlines() if ln.strip()]
    if os.path.exists(DISABLED):
        log(f"DISABLED file present: accepted push without checks ({len(updates)} ref(s))")
        print("lumen-commit-check: WARNING policy check DISABLED by "
              f"{DISABLED} (break glass)", file=sys.stderr)
        return 0
    if exempt(repo_slug()):
        return 0
    if not (os.path.isfile(os.path.join(SITE, "commit_check", "main.py"))
            and os.path.isfile(CONFIG)):
        refuse([f"policy checker not installed ({SITE} / {CONFIG}); failing closed"])
    t0, failures, seen = time.monotonic(), [], set()
    for parts in updates:
        if len(parts) != 3:
            refuse([f"malformed pre-receive line: {' '.join(parts)!r}"])
        old, new, ref = parts
        if new == ZERO or not ref.startswith("refs/heads/"):
            continue
        for sha in new_commits(new):
            if sha in seen:
                continue
            seen.add(sha)
            if time.monotonic() - t0 > BUDGET_S:
                refuse([f"time budget {BUDGET_S:.0f}s exhausted after {len(seen) - 1} "
                        f"commit(s); push fewer commits at a time"])
            rc, out = check(sha)
            if rc != 0:
                kind = "policy" if rc == 1 else f"checker error rc={rc}"
                failures.append(f"{sha[:10]} on {ref[11:]} ({kind}):")
                failures += ["    " + ln for ln in out.splitlines()[:12]]
                if sum(1 for f in failures if not f.startswith("    ")) >= MAX_REPORT:
                    failures.append(f"(stopped after {MAX_REPORT} failing commits)")
                    break
    if failures:
        log(f"refused push: {len([f for f in failures if not f.startswith('    ')])} "
            f"failing commit(s)")
        refuse(failures)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:  # fail closed on anything unexpected, incl. timeouts
        log(f"internal error, refusing: {type(e).__name__}: {e}")
        refuse([f"internal error ({type(e).__name__}: {str(e)[:200]}); failing closed"])
