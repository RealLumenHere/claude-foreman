"""wq#2913 acceptance probe: kb/reports/wq2868_tail_probe.py's four cases, run against
EVERY mirror of the house rule set instead of only harness/masking.py.

wq#2868's probe measured masking.py alone and found the tail case leaking 200/200 on
both main and wq#2848's fix. The ticket's acceptance is "0 leaks on all 4 cases for
every mirror", so the mirrors need the same instrument: token_engine_daemon's
mask_rewrite (the broker's copy, which runs as `tokeneng` off the harness import path)
and bin/mask-secrets (the pipe instinct 4 points every agent at).

Values are assembled at runtime from a seeded RNG and only COUNTS are printed, never a
value — the wq#2868 probe's rule, for the reason test_the_mask_suite_does_not_arm_its
_own_tripwire gives. The wrapper is neutral text (`see <tok> end`): a `token=` wrapper
fires kv-secret, masks the whole value and hides the defect, a confound wq#2868 found
and removed during its own run.

Usage: python3 probe_mirrors.py [<repo root>]     (default: this bundle's own repo)
Exit status is 0 only if every mirror leaks nothing on every case.
"""
import importlib.util
import os
import random
import string
import subprocess
import sys

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1
                       else os.path.join(os.path.dirname(__file__), "..", ".."))
AN = string.ascii_letters + string.digits
UD = string.ascii_uppercase + string.digits
GH = "gh" + "p_"
AWS = "AK" + "IA"
N = 200


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, os.path.join(ROOT, "harness"))
sys.path.insert(0, os.path.join(ROOT, "token-engine"))
import fworker  # noqa: E402,F401  (side effect: puts the host's fleet_exec_policy on
#                                   sys.path, which the daemon imports; same trick
#                                   harness/tests/test_masking.py uses)
masking = load("masking_under_test", os.path.join(ROOT, "harness", "masking.py"))
ted = load("ted_under_test", os.path.join(ROOT, "token-engine", "token_engine_daemon.py"))
WRAPPER = os.path.join(ROOT, "ops", "wq2316-mask-secrets-boundary", "mask-secrets")


def via_wrapper(text):
    return subprocess.run(["bash", WRAPPER], input=text, capture_output=True, text=True,
                          check=True).stdout


MIRRORS = [
    ("masking.py", lambda t: masking.mask_text(t)[0]),
    ("token_engine_daemon", ted.mask_rewrite),
    ("bin/mask-secrets", via_wrapper),
]


def leaked(body, out):
    """Longest run of `body` (>= 4 chars) that survived verbatim — wq#2868's metric."""
    best = 0
    for i in range(len(body)):
        for j in range(i + 4, len(body) + 1):
            if body[i:j] not in out:
                break
            best = max(best, j - i)
    return best


def cases(rng):
    def rnd(charset, n):
        return "".join(rng.choice(charset) for _ in range(n))
    return {
        # the wq#2913 shape: body past the floor, a FULL foreign shape, then a tail with
        # no prefix of its own — what a stop-based design could never re-cover
        "gh32+aws+tail8": GH + rnd(AN, 32) + AWS + rnd(UD, 16) + rnd(AN, 8),
        "gh40+aws+tail4": GH + rnd(AN, 40) + AWS + rnd(UD, 16) + rnd(AN, 4),
        # the wq#2848 regression shape: bare prefix noise inside the floor
        "gh+bareAKIA@12": GH + rnd(AN, 12) + AWS + rnd(AN, 24),
        "gh36 control": GH + rnd(AN, 36),
    }


def main():
    failures = 0
    for name, mask in MIRRORS:
        print("probe", name)
        rng = random.Random(2868)       # wq#2868's seed, so the runs are comparable
        counts = {case: [0, 0] for case in cases(random.Random(0))}
        for _ in range(N):
            for case, token in cases(rng).items():
                run = leaked(token[len(GH):], mask("see " + token + " end"))
                if run >= 4:
                    counts[case][0] += 1
                    counts[case][1] = max(counts[case][1], run)
        for case, (leaks, longest) in counts.items():
            print("  %-16s leaked %3d/%d  longest_surviving_run=%d"
                  % (case, leaks, N, longest))
            failures += leaks
    print("FAIL" if failures else "OK", "- total leaked trials:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
