#!/usr/bin/env python3
"""minerva_guard.py — wq#3147.

minerva has no CI workflow and no Gitea runner, and `lumen-pr land` merges a repo
with zero checks on review alone (bin/lumen-pr, "NO CI CHECKS ... landing on
review alone"). So the wq#2900 sqlite leak ratchet, and every other minerva test,
only guarded when someone ran them by hand: tests/test_sqlite_leak_ratchet.py sat
red on main from ebfcda4 (2026-09-28 13:52Z) to 864fe16 (2026-09-29) and nothing
said so (wq#3146).

This runs minerva's tracked pytest suite against a clean export of a git rev
(default origin/main, fetched first) — never the primary checkout, whose
uncommitted edits would both mask and fake failures — and pages on red.

    minerva_guard.py run   [--rev REF] [--repo DIR] [--state PATH] [--dry-run] [--no-fetch]
        timer mode: evaluates, pages (aq + ntfy) on a new red or a changed failing
        set, resolves the aq row on green. Exit 0 once it has evaluated and
        reported, whatever the verdict, so the systemd unit (and its cron_deadman
        stamp) tracks "the guard ran", not "minerva is green".
    minerva_guard.py check [--rev REF] [--repo DIR] [--no-fetch]
        manual pre-land check, e.g. `check --rev <branch>` in the minerva repo.
        No state, no alerts. Exit 0 green, 1 red, 2 the guard could not run.

The guard pages on its own breakage too (fetch/export failure, pytest timeout or
crash, no tests collected): a guard that silently stops running is the failure
this ticket exists to remove.
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time

DEFAULT_REPO = "/home/lumen/minerva"
DEFAULT_REV = "origin/main"
DEFAULT_STATE = "/home/lumen/logs/.lumen-minerva-guard.state.json"
NTFY_BIN = "/home/lumen/bin/ntfy-send"
AQ_DIR = os.environ.get("MINERVA_GUARD_AQ_DIR", "/home/lumen/agents/alert-queue")
SOURCE = "minerva-guard"
KEY_RED = "minerva-tests-red"
KEY_BROKEN = "minerva-guard-broken"
PYTEST_TIMEOUT_S = 900
TAIL_CHARS = 1500

_FAILED_RE = re.compile(r"^(FAILED|ERROR) (\S+)")


def git(repo, *args, binary=False, timeout=120):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                          text=not binary, timeout=timeout)


def resolve_rev(repo, rev, fetch):
    """Returns (sha, error). Fetches origin/main first so the timer sees what has
    landed, not what the primary checkout last pulled."""
    if fetch:
        r = git(repo, "fetch", "-q", "origin", "main")
        if r.returncode != 0:
            return None, f"git fetch origin main failed: {r.stderr.strip()[-300:]}"
    r = git(repo, "rev-parse", "--verify", "-q", rev + "^{commit}")
    if r.returncode != 0:
        return None, f"cannot resolve rev {rev!r} in {repo}"
    return r.stdout.strip(), None


def export_tree(repo, sha, dest):
    """Extracts the committed tree at sha into dest (git archive, no worktree, no
    change to the primary checkout's index or files)."""
    r = git(repo, "archive", sha, binary=True, timeout=300)
    if r.returncode != 0:
        return f"git archive {sha[:12]} failed: {r.stderr.decode(errors='replace').strip()[-300:]}"
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tf:
        tf.extractall(dest, filter="data")
    return None


def parse_failures(output):
    """Test ids from pytest's -rfE short summary (FAILED/ERROR lines)."""
    ids = []
    for line in output.splitlines():
        m = _FAILED_RE.match(line)
        if m and m.group(2) not in ids:
            ids.append(m.group(2))
    return ids


def run_suite(tree, timeout=PYTEST_TIMEOUT_S):
    """Returns (verdict, failing_ids, tail). verdict: green | red | broken.
    pytest exit codes: 0 all passed, 1 some failed, 5 nothing collected,
    anything else is an internal/usage error — the guard itself is broken."""
    env = dict(os.environ)
    # test_scout_insert_rollback loads the primary checkout's module unless told
    # otherwise; point it at the export so the rev under test is what is tested.
    env["SCOUT_PATH"] = os.path.join(tree, "lumen_scout_expanded.py")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cmd = [sys.executable, "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider", "tests"]
    try:
        r = subprocess.run(cmd, cwd=tree, capture_output=True, text=True,
                           timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return "broken", [], f"pytest timed out after {timeout}s"
    out = (r.stdout or "") + (r.stderr or "")
    tail = out[-TAIL_CHARS:]
    if r.returncode == 0:
        return "green", [], tail
    if r.returncode == 1:
        return "red", parse_failures(out), tail
    return "broken", [], f"pytest exit {r.returncode}\n{tail}"


def evaluate(repo, rev, fetch):
    """Returns a result dict: verdict, sha, failing, detail."""
    try:
        sha, err = resolve_rev(repo, rev, fetch)
        if err:
            return {"verdict": "broken", "sha": None, "failing": [], "detail": err}
        with tempfile.TemporaryDirectory(prefix="minerva-guard-") as tree:
            err = export_tree(repo, sha, tree)
            if err:
                return {"verdict": "broken", "sha": sha, "failing": [], "detail": err}
            if not os.path.isdir(os.path.join(tree, "tests")):
                return {"verdict": "broken", "sha": sha, "failing": [],
                        "detail": f"no tests/ dir at {sha[:12]}"}
            verdict, failing, tail = run_suite(tree)
    except (OSError, subprocess.SubprocessError, tarfile.TarError) as e:
        return {"verdict": "broken", "sha": None, "failing": [],
                "detail": f"{type(e).__name__}: {e}"}
    return {"verdict": verdict, "sha": sha, "failing": failing, "detail": tail}


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def ntfy(title, message, priority="default", dry_run=False):
    if dry_run:
        print(f"[dry-run] ntfy {priority} {title!r}: {message}")
        return
    try:
        subprocess.run([NTFY_BIN, "-p", priority, "-T", title, message],
                       capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        pass  # aq still carries the alert


def aq_mod():
    sys.path.insert(0, AQ_DIR)
    import aq
    return aq


def aq_ingest(key, title, message, priority, dry_run=False):
    if dry_run:
        print(f"[dry-run] aq {priority} {key}: {title}")
        return None
    try:
        return aq_mod().ingest(source=SOURCE, key=key, title=title,
                               message=message, priority=priority)
    except Exception as e:  # noqa: BLE001 — a queue fault must not stop the report
        print(f"minerva-guard: aq ingest failed for {key}: {e}", file=sys.stderr)
        return None


def aq_resolve(rid, note, dry_run=False):
    if dry_run or rid is None:
        return
    try:
        aq_mod().set_status(rid, "resolved", note)
    except Exception as e:  # noqa: BLE001
        print(f"minerva-guard: aq resolve failed for #{rid}: {e}", file=sys.stderr)


def report(result, prev, rev, dry_run=False, now=None):
    """Edge-triggered paging. Returns the new state.

    red:    page on entering red, or when the failing set changes while red (a
            second regression landing on an already-red main must not hide).
    broken: page once per broken streak; the open red row, if any, stays open.
    green:  resolve the red row and send one RECOVERED ntfy.
    """
    now = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    verdict, sha, failing = result["verdict"], result["sha"], result["failing"]
    short = (sha or "?")[:12]
    # code_verdict is the last green/red actually observed; a broken run says
    # nothing about the code, so it must not reset the red edge (else a red ->
    # broken -> same red sequence would page twice).
    prev_code = prev.get("code_verdict")
    state = {"verdict": verdict, "sha": sha, "rev": rev, "checked": now,
             "code_verdict": prev_code, "failing": prev.get("failing") or [],
             "since": prev.get("since"), "red_aq_id": prev.get("red_aq_id"),
             "broken_aq_id": prev.get("broken_aq_id")}

    if verdict == "broken":
        if prev.get("verdict") != "broken":
            title = "minerva-guard cannot run the minerva suite"
            rid = aq_ingest(KEY_BROKEN, title, result["detail"][-800:], "default", dry_run)
            state["broken_aq_id"] = rid
            ntfy(title, result["detail"][-800:], "default", dry_run)
        return state

    if prev.get("verdict") == "broken":
        aq_resolve(state["broken_aq_id"], f"guard ran again at {short}", dry_run)
        state["broken_aq_id"] = None
    state.update(code_verdict=verdict, failing=failing,
                 since=prev.get("since") if prev_code == verdict else now)

    if verdict == "red":
        if prev_code != "red" or sorted(prev.get("failing") or []) != sorted(failing):
            title = f"minerva tests RED at {rev} {short} ({len(failing)} failing)"
            msg = ("Failing: " + ", ".join(failing[:10])
                   + (f" (+{len(failing) - 10} more)" if len(failing) > 10 else "")
                   + f"\nRepro: python3 {os.path.abspath(__file__)} check --rev {short}"
                   + f"\n--- pytest tail ---\n{result['detail'][-800:]}")
            rid = aq_ingest(KEY_RED, title, msg, "high", dry_run)
            state["red_aq_id"] = rid if rid is not None else state["red_aq_id"]
            ntfy(title, msg, "high", dry_run)
    else:
        if prev_code == "red":
            aq_resolve(state["red_aq_id"], f"green at {short}", dry_run)
            ntfy(f"minerva tests RECOVERED at {rev} {short}",
                 f"was red since {prev.get('since')}", "default", dry_run)
        state["red_aq_id"] = None
    return state


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["run", "check"])
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--rev", default=DEFAULT_REV)
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--no-fetch", action="store_true",
                    help="skip `git fetch origin main` (offline / local refs)")
    ap.add_argument("--dry-run", action="store_true",
                    help="run: print alerts instead of sending, do not write state")
    a = ap.parse_args(argv)

    result = evaluate(a.repo, a.rev, fetch=not a.no_fetch)
    sha = (result["sha"] or "?")[:12]
    print(f"minerva-guard: {result['verdict'].upper()} {a.rev} {sha}"
          + (f" failing={','.join(result['failing'])}" if result["failing"] else ""))
    if result["verdict"] != "green":
        print(result["detail"])

    if a.cmd == "check":
        return {"green": 0, "red": 1}.get(result["verdict"], 2)
    prev = load_state(a.state)
    state = report(result, prev, a.rev, dry_run=a.dry_run)
    if not a.dry_run:
        save_state(a.state, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
