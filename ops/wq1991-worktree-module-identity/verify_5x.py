#!/usr/bin/env python3
"""wq#1991 acceptance: 5x repeated full `harness/tests` run from THIS worktree, against a
deliberately-diverged primary tree, must come back identical and reference only this
worktree's own modules.

The divergence is real and sits in exactly the relevant files: `/home/lumen/foreman`
still carries the pre-fix literals (`despag/admission.py:54-56` hardcoding
`/home/lumen/foreman/{governor,dispatcher,harness}`) and has no copy of
`harness/tests/test_worktree_module_identity.py`. A leak therefore resolves a DIFFERENT
file than this tree's and shows up as a diff, which is the whole point — no mutation of
the primary checkout is needed to create the condition.

Usage: python3 ops/wq1991-worktree-module-identity/verify_5x.py <outdir>
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNS = 5

# The two deliberately-nondeterministic parts of pytest -q output: the wall clock in the
# summary line, and tmp_path/pid strings inside failure reports.
SCRUB = (
    (re.compile(r" in \d+\.\d+s"), " in <elapsed>"),
    # pytest appends `(H:MM:SS)` to the summary once a run passes a minute; it drifts by
    # a second or two between repeats and is not a result difference.
    (re.compile(r"\(\d+:\d{2}:\d{2}\)"), "(<hms>)"),
    (re.compile(r"/tmp/pytest-of-\S+"), "<tmp>"),
    (re.compile(r"\b\d{4,7}\b"), "<num>"),
)

LEAK_PROBE = r"""
import os, sys
REPO = os.path.realpath(sys.argv[1])
sys.path.insert(0, os.path.join(REPO, "harness"))
sys.path.insert(0, REPO)
import despag.app  # noqa: F401
foreign = sorted(
    "%s <- %s" % (n, os.path.realpath(m.__file__))
    for n, m in sys.modules.items()
    if getattr(m, "__file__", None)
    and os.path.realpath(m.__file__).startswith("/home/lumen/foreman/")
    and not os.path.realpath(m.__file__).startswith(REPO + os.sep))
print("FOREIGN_MODULES %d" % len(foreign))
print("\n".join(foreign))
"""


def scrub(text: str) -> str:
    for pat, repl in SCRUB:
        text = pat.sub(repl, text)
    return text


def main() -> int:
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    # A transcript left behind by an earlier, differently-configured run reads exactly
    # like a nondeterministic repeat when you diff the directory afterwards. Clear first.
    for stale in os.listdir(out):
        if re.fullmatch(r"(run|leak)\d+\.txt", stale):
            os.unlink(os.path.join(out, stale))
    digests, leaks = [], []
    for i in range(1, RUNS + 1):
        suite = subprocess.run(
            [sys.executable, "-m", "pytest", "harness/tests", "-q", "--tb=no", "-rf",
             "-p", "no:cacheprovider"],
            cwd=REPO, capture_output=True, text=True)
        body = scrub(suite.stdout + suite.stderr)
        open(os.path.join(out, f"run{i}.txt"), "w").write(body)
        digests.append(hashlib.md5(body.encode()).hexdigest())

        probe = subprocess.run([sys.executable, "-c", LEAK_PROBE, REPO],
                               cwd=REPO, capture_output=True, text=True)
        open(os.path.join(out, f"leak{i}.txt"), "w").write(probe.stdout + probe.stderr)
        leaks.append(next((ln for ln in probe.stdout.splitlines()
                           if ln.startswith("FOREIGN_MODULES")), "PROBE_FAILED"))
        print(f"run {i}: {digests[-1]}  {leaks[-1]}", flush=True)

    print("--- last run summary")
    print("\n".join(open(os.path.join(out, f"run{RUNS}.txt")).read().splitlines()[-3:]))
    identical = len(set(digests)) == 1
    clean = all(ln == "FOREIGN_MODULES 0" for ln in leaks)
    print(f"--- BYTE_IDENTICAL={identical} FOREIGN_FREE={clean}")
    return 0 if (identical and clean) else 1


if __name__ == "__main__":
    sys.exit(main())
