#!/usr/bin/env python3
"""wq#5283 adversarial probe of wq#5251 (PR #2717, commit 55336497).

wq#5251 dropped venabled/vactive from ops/wq1294-session-handoff/install.sh --verify,
calling session-handoff.timer intended-off. This probe checks, against the bundle
PINNED at git revs (not the live working tree), that:

  A. the post-fix bundle verifies OK against the live /etc units (liveness predicate);
  B. the reduced verify can still FAIL: a one-byte tamper of the bundle's .service
     makes it STILL_BLOCKED (so the fix did not turn --verify into a no-op);
  C. the pre-fix bundle (55336497^) still FAILS live on "not enabled/not active",
     i.e. the diff is exactly what flips the predicate;
  D. the timer is still disabled+inactive live: the fix did not get there by
     re-enabling it (which would undo the 2026-10-08 subtraction);
  E. the subtraction list really names the timer at the line the comment cites.

Run: python3 reports/wq5283_verify_wq5251_probe.py   (exit 0 = all hold)
"""
import os
import subprocess
import sys
import tempfile

REPO = "/home/lumen/foreman"
FIX = "553364970a33617d92fef6159e3365520faf6648"
BUNDLE = "wq1294-session-handoff"
FILES = ("install.sh", "session-handoff.service", "session-handoff.timer")
SUBTRACTION = "/home/lumen/scratch/subtraction/timers-disable-phase2.txt"

sys.path.insert(0, os.path.join(REPO, "harness"))
import liveness  # noqa: E402


def git_show(rev, path):
    return subprocess.run(["git", "-C", REPO, "show", f"{rev}:{path}"],
                          check=True, capture_output=True).stdout


def stage(root, rev, tamper=False):
    os.makedirs(os.path.join(root, BUNDLE), exist_ok=True)
    with open(os.path.join(root, "_verify.sh"), "wb") as f:
        f.write(git_show(rev, "ops/_verify.sh"))
    for name in FILES:
        body = git_show(rev, f"ops/{BUNDLE}/{name}")
        if tamper and name == "session-handoff.service":
            body += b"# tampered by wq5283 probe\n"
        with open(os.path.join(root, BUNDLE, name), "wb") as f:
            f.write(body)


def run(rev, tamper=False):
    with tempfile.TemporaryDirectory(prefix="wq5283-") as root:
        stage(root, rev, tamper)
        saved, liveness.OPS_ROOT = liveness.OPS_ROOT, root
        try:
            return liveness.ops_verified(BUNDLE)
        finally:
            liveness.OPS_ROOT = saved


def systemctl(verb):
    return subprocess.run(["systemctl", verb, "session-handoff.timer"],
                          capture_output=True, text=True).stdout.strip()


checks = []
a = run(FIX)
checks.append(("A post-fix bundle verifies live", a[0] == liveness.SATISFIED, a))
b = run(FIX, tamper=True)
checks.append(("B tampered unit still fails", b[0] == liveness.STILL_BLOCKED
               and "differs" in b[1], b))
c = run(FIX + "^")
checks.append(("C pre-fix bundle fails on enable/active", c[0] == liveness.STILL_BLOCKED
               and "not enabled" in c[1], c))
d = (systemctl("is-enabled"), systemctl("is-active"))
checks.append(("D timer still intended-off live", d == ("disabled", "inactive"), d))
with open(SUBTRACTION) as f:
    line54 = f.read().splitlines()[53].strip()
checks.append(("E subtraction list line 54", line54 == "session-handoff.timer", line54))

bad = 0
for name, ok, detail in checks:
    print(("PASS " if ok else "FAIL ") + name + " :: " + str(detail))
    bad += not ok
sys.exit(1 if bad else 0)
