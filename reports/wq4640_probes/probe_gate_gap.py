#!/usr/bin/env python3
"""wq#4640: show WHY wq#4638 closed done on an undeployed bundle, so the standing fix
targets the real mechanism rather than the symptom.

`wq done`'s ops-deploy gate implicates a bundle only when the cited PR touches that
bundle's install.sh: pr_ops_deploy_check filters the PR's changed files through
OPS_INSTALL_RE (/home/lumen/bin/wq:2855, applied at :3115) and returns verdict
"none" -- fail open, close clean -- when nothing matches. A nodes.notes commit touches
only ops/<bundle>/selfheal.yaml, so no bundle is identified and no --verify runs.

The regex is read out of the live wq script at runtime rather than copied here, so if
the gate is later widened this probe's expectation flips instead of silently passing.

Rev-pinned inputs (re-runnable, nothing mutable read):
  e7d2dd76  wq#4638's commit -- the real closing citation's content. Expect: no match,
            i.e. the gate would NOT have fired. This is the bug.
  8eaff1e4  a control commit that does not touch ops/ at all. Expect: no match, for
            the right reason.
  A synthetic install.sh path. Expect: match -- proof the predicate is not just
            always-false and the probe can distinguish.
"""
import pathlib
import re
import subprocess
import sys

WQ = pathlib.Path("/home/lumen/bin/wq")
REPO = pathlib.Path(__file__).resolve().parents[2]
BUNDLE_DIR = "ops/wq3354-fleet-selfheal"


def live_regex():
    for line in WQ.read_text().splitlines():
        m = re.match(r"OPS_INSTALL_RE\s*=\s*re\.compile\(r\"(.*)\"\)\s*$", line)
        if m:
            return m.group(1)
    raise SystemExit("OPS_INSTALL_RE not found in /home/lumen/bin/wq")


def changed(rev):
    out = subprocess.run(["git", "-C", str(REPO), "show", "--name-only",
                          "--format=", rev],
                         capture_output=True, text=True, check=True).stdout
    return [l for l in out.splitlines() if l.strip()]


def main():
    pat = live_regex()
    rx = re.compile(pat)
    print(f"gate predicate (wq:2855): {pat}")
    fails = []

    for rev, label in [("e7d2dd76", "wq#4638 nodes.notes commit"),
                       ("8eaff1e4", "control, no ops/ files")]:
        files = changed(rev)
        hits = [f for f in files if rx.search("/" + f)]
        print(f"  {rev} ({label}): files={len(files)} gate_hits={hits}")
        if hits:
            fails.append(rev)

    synth = f"{BUNDLE_DIR}/install.sh"
    hit = bool(rx.search("/" + synth))
    print(f"  synthetic {synth}: gate_hit={hit}")
    if not hit:
        fails.append("synthetic")

    # And the file wq#4638 DID touch sits in a bundle dir that has an install.sh --
    # so a dir-scoped predicate would have caught it.
    touched = [f for f in changed("e7d2dd76") if f.startswith(BUNDLE_DIR + "/")]
    has_install = (REPO / BUNDLE_DIR / "install.sh").exists()
    print(f"  touched files inside {BUNDLE_DIR}: {touched}")
    print(f"  that dir ships an install.sh: {has_install}")
    if not (touched and has_install):
        fails.append("dir-scoped-premise")

    print("PROBE PASS (gate gap reproduced)" if not fails
          else f"PROBE FAIL: {','.join(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
