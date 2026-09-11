#!/usr/bin/env python3
"""primary_tree_guard — assert every registered PRIMARY tree is on its default branch.

wq#900: the live dispatcher/self-heal/health timers execute from /home/lumen/foreman's
PRIMARY checkout, and an audit found a worker had left it on a feature branch
(research/wq633-849-headroom-judge) — production was running unreviewed code. Same
class hit /home/lumen/bin (wq#867). LANE_BOUNDARIES mandates worktrees and lumen-pr
refuses --primary, but nothing stopped a raw `git checkout` in the primary. This
module is two of the three walls (guard.sh's wq#900 deny class is the first):

  - timer mode (`check`): every few minutes, assert each primary tree's HEAD is its
    default branch; ntfy-alert on drift and auto-restore ONLY when the tree has no
    modified tracked files (never clobber work — a dirty drifted tree alerts and
    waits for a human).
  - post-run mode: fworker calls check_and_restore_all() after every child exits,
    so a worker that evaded the guard is caught within seconds of finishing.

Exit codes (CLI): 0 = all primaries on default branch (or restored this run),
1 = at least one still drifted, 2 = internal error.
"""
import json
import os
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")
STATE_PATH = os.environ.get("PRIMARY_TREE_GUARD_STATE",
                            "/tmp/.primary-tree-guard.state")

# Static mirror of router.LANE_REPO's values + /home/lumen/bin (repo_sweep's
# EXTRA_REPOS). The import below is authoritative; this list exists so a broken
# import path can never silently shrink the guard's scope to nothing.
#
# wq#1752 (ISSUE-LOG #302, wq#1733's verify-pass of wq#1719/PR#426): kb and the
# shallow nest archive are both granted as default add_dirs (agents.yaml) but
# neither was reachable through router.LANE_REPO — kb has no LANE_REPO entry at
# all, and LANE_REPO["nest"] points one level deeper (.../nest/NoLongerEvil-
# Thermostat) than the shallow /home/lumen/projects/nest root actually granted.
# That let a worker Edit/Write inside either tree despite fworker setting
# FOREMAN_PRIMARY_TREES from this function's own output. Listed here (not
# LANE_REPO) because LANE_REPO is keyed by dispatch lane, not guard scope, and
# already has a distinct 'nest' entry for the deeper firmware repo.
STATIC_PRIMARY_TREES = [
    "/home/lumen/foreman",
    "/home/lumen/bin",
    "/home/lumen/pokemon",
    "/home/lumen/questfree",
    "/home/lumen/pstack",
    "/home/lumen/mission-control",
    "/home/lumen/kb",
    "/home/lumen/projects/nest",
]


def primary_trees():
    """Registered primary trees: FOREMAN_PRIMARY_TREES env override (colon-separated,
    same variable guard.sh reads), else router.LANE_REPO values unioned with the
    static mirror."""
    env = os.environ.get("FOREMAN_PRIMARY_TREES")
    if env:
        return sorted({p for p in env.split(":") if p})
    trees = set(STATIC_PRIMARY_TREES)
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(HARNESS_DIR), "orchestrator"))
        from router import LANE_REPO  # noqa: E402
        trees |= set(LANE_REPO.values())
    except Exception:
        pass
    return sorted(trees)


def _git(repo, *args, timeout=30):
    return subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True, timeout=timeout)


def default_branch(repo):
    """The tree's deployment branch: main, then master, else None."""
    for cand in ("main", "master"):
        p = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{cand}")
        if p.returncode == 0:
            return cand
    return None


def check_tree(repo):
    """One tree's verdict: {repo, head, default, drifted, dirty, error}."""
    r = {"repo": repo, "head": None, "default": None, "drifted": False,
         "dirty": False, "error": None}
    if not os.path.isdir(os.path.join(repo, ".git")):
        r["error"] = "not a git repo"
        return r
    try:
        r["head"] = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        r["default"] = default_branch(repo)
        if r["default"] is None:
            r["error"] = "no main/master branch"
            return r
        r["drifted"] = r["head"] != r["default"]
        if r["drifted"]:
            # Modified tracked files only — untracked droppings (e.g. wq#634's
            # PXPIPE_CANARY.md) don't block a branch switch, so they don't block
            # the restore either; they're repo_sweep's problem, not ours.
            r["dirty"] = bool(_git(repo, "status", "--porcelain",
                                   "--untracked-files=no").stdout.strip())
    except Exception as e:
        r["error"] = str(e)
    return r


def restore_tree(repo, default):
    """git checkout <default>. Returns (ok, output). git itself refuses a checkout
    that would clobber local modifications, so this is safe even if the dirty
    check raced."""
    p = _git(repo, "checkout", default)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def alert(title, msg):
    try:
        subprocess.run([NTFY_BIN, "--title", title, "--priority", "urgent", msg],
                       capture_output=True, text=True, timeout=10)
    except Exception:
        pass


def check_and_restore_all(repos=None, restore=True, do_alert=True, context="",
                          log=None):
    """Check every primary; restore clean drifted trees; alert on any drift.
    Returns the list of drifted results (each annotated with restored/restore_out).
    Never raises for a single bad repo — the sweep must reach every tree."""
    drifted = []
    for repo in (repos if repos is not None else primary_trees()):
        r = check_tree(repo)
        if not r["drifted"]:
            continue
        r["restored"] = False
        r["restore_out"] = ""
        if restore and not r["dirty"] and not r["error"]:
            r["restored"], r["restore_out"] = restore_tree(repo, r["default"])
        if do_alert:
            suffix = f" ({context})" if context else ""
            if r["restored"]:
                body = (f"{repo} was on '{r['head']}' (default {r['default']}) — "
                        f"auto-restored{suffix}. A worker switched a PRIMARY tree; "
                        f"find it in the events log.")
            else:
                why = "dirty tracked files" if r["dirty"] else (r["error"] or
                                                                "restore failed")
                body = (f"{repo} is on '{r['head']}' (default {r['default']}) and NOT "
                        f"auto-restored: {why}{suffix}. Manual: "
                        f"git -C {repo} checkout {r['default']}")
            alert("PRIMARY tree off default branch (wq#900)", body)
        if log:
            log(f"primary_tree_guard: {repo} head={r['head']} "
                f"default={r['default']} restored={r['restored']}")
        drifted.append(r)
    return drifted


def _load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", nargs="?", default="check", choices=["check", "status"],
                    help="check = alert + auto-restore-when-clean (timer mode); "
                         "status = report only, no alert, no restore")
    ap.add_argument("--repo", action="append", default=None,
                    help="override: check only these trees (repeatable)")
    a = ap.parse_args(argv)

    if a.mode == "status":
        rc = 0
        for repo in (a.repo if a.repo is not None else primary_trees()):
            r = check_tree(repo)
            if r["error"]:
                print(f"ERROR    {repo}: {r['error']}")
            elif r["drifted"]:
                print(f"DRIFTED  {repo}: on '{r['head']}', default '{r['default']}'"
                      f"{' (dirty)' if r['dirty'] else ''}")
                rc = 1
            else:
                print(f"OK       {repo}: {r['head']}")
        return rc

    # check mode: dedup un-restorable drift alerts across timer runs the same way
    # wq-drift-guard does — alert once per (repo, wrong-branch) episode, clear the
    # record once the tree is clean again. A successful auto-restore always alerts
    # (it is the one-shot signal that a worker touched a primary).
    state = _load_state()
    drifted = check_and_restore_all(repos=a.repo, restore=True, do_alert=False,
                                    context="timer")
    still_bad = 0
    seen = set()
    for r in drifted:
        seen.add(r["repo"])
        if r["restored"]:
            state.pop(r["repo"], None)
            alert("PRIMARY tree off default branch (wq#900)",
                  f"{r['repo']} was on '{r['head']}' (default {r['default']}) — "
                  f"auto-restored by timer. A worker switched a PRIMARY tree.")
            print(f"RESTORED {r['repo']}: '{r['head']}' -> '{r['default']}'")
            continue
        still_bad += 1
        why = "dirty" if r["dirty"] else (r["error"] or "restore failed")
        print(f"DRIFTED  {r['repo']}: on '{r['head']}' ({why}) — not restored")
        if state.get(r["repo"]) != r["head"]:
            state[r["repo"]] = r["head"]
            alert("PRIMARY tree off default branch (wq#900)",
                  f"{r['repo']} is on '{r['head']}' (default {r['default']}), "
                  f"{why} — NOT auto-restored. Manual: git -C {r['repo']} "
                  f"checkout {r['default']}")
    for repo in list(state):
        if repo not in seen:
            state.pop(repo)
    _save_state(state)
    return 1 if still_bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"primary_tree_guard: internal error: {e}", file=sys.stderr)
        sys.exit(2)
