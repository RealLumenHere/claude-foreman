#!/usr/bin/env python3
"""wq#5281 verify-pass probe for wq#5256 (PR #2715): can the wq1640 bundle's --verify FAIL?

Materialises the bundle + ops/_verify.sh from rev-pinned git objects into temp OPS_ROOTs and
runs them through the harness's own liveness.ops_verified (the Liveness: probe the ticket
names), so every input except live host state (/etc/systemd/system, systemctl, minerva) is
immutable.

  A  merged fix (32f2a028)                         -> expect SATISFIED
  B  pre-fix parent (32f2a028^), timer disabled    -> expect STILL_BLOCKED (not enabled/active)
  C  merged fix with the bundle's .timer mutated   -> expect STILL_BLOCKED (differs)

Exit 0 only when all three verdicts match.
"""
import os
import subprocess
import sys
import tempfile

REPO = "/home/lumen/foreman"
FIX = "32f2a02835a061dc8da3084db62da607372d60a8"
BUNDLE = "wq1640-lumen-state-render-frank-side-hardware-q-a3"
sys.path.insert(0, os.path.join(REPO, "harness"))
import liveness  # noqa: E402


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], check=True, capture_output=True).stdout


def materialise(rev, root):
    os.makedirs(os.path.join(root, BUNDLE))
    for rel in ["ops/_verify.sh"] + git("ls-tree", "-r", "--name-only", rev,
                                        f"ops/{BUNDLE}/").decode().split():
        dst = os.path.join(root, rel[len("ops/"):])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(git("show", f"{rev}:{rel}"))


def run(label, rev, expect, mutate=None):
    with tempfile.TemporaryDirectory(prefix="wq5281-") as root:
        materialise(rev, root)
        if mutate:
            with open(os.path.join(root, BUNDLE, mutate), "a") as fh:
                fh.write("# wq5281 mutation\n")
        liveness.OPS_ROOT = root
        verdict, detail = liveness.ops_verified(BUNDLE)
    ok = verdict == expect
    print(f"{'PASS' if ok else 'FAIL'} {label}: {verdict} (want {expect}) :: {detail}")
    return ok


results = [
    run("A merged fix", FIX, "SATISFIED"),
    run("B pre-fix parent", FIX + "^", "STILL_BLOCKED"),
    run("C fix + mutated timer", FIX, "STILL_BLOCKED", mutate="prefs-block-check.timer"),
]
sys.exit(0 if all(results) else 1)
