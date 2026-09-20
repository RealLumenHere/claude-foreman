#!/usr/bin/env python3
"""wq#2333 — score a PR body against the DEPLOYED token-engine tripwire.

The wq#2292 word-boundary fix landed in-repo but the /usr/local/lib copy the
broker executes went unredeployed for a day, so `lumen-pr open` denied any PR
body that quoted ISSUE-LOG's own `highs_solver` example (row 568). This probe
asks the deployed file — not the checkout — whether a body passes, which is the
only copy that can refuse a PR.

  ops/wq2333-token-engine-redeploy/check_body.py [body-file]

Exit 0 iff the deployed rules score the body at zero hits. The pre-wq#2292 rule
is scored alongside it: a fixture both rules accept proves nothing, so the probe
fails loudly if the old rule does not fire on the fixture it ships with.
"""

import importlib.util
import os
import re
import sys

DEPLOYED = "/usr/local/lib/token-engine/token_engine_daemon.py"
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "body_fixture.md")

# The rule as it stood before wq#2292: no left boundary, 6-char body floor.
OLD_GITHUB_RE = re.compile(r"(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{6,}")


def load_deployed():
    if not os.path.exists(DEPLOYED):
        sys.exit(f"FAIL: {DEPLOYED} absent — token-engine is not installed")
    spec = importlib.util.spec_from_file_location("te_deployed", DEPLOYED)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    body_path = sys.argv[1] if len(sys.argv) > 1 else FIXTURE
    with open(body_path, errors="replace") as f:
        text = f.read()

    old_hits = sorted({m.group(1) for m in OLD_GITHUB_RE.finditer(text)})
    deployed_hits = load_deployed().mask_hits(text)

    print(f"body            : {body_path} ({len(text)} bytes)")
    print(f"pre-wq2292 rule : {len(old_hits)} prefix hit(s) {old_hits}")
    print(f"deployed rules  : {deployed_hits or 'none'}")

    if not old_hits:
        sys.exit("FAIL: fixture does not reproduce the bug — the old rule scores it clean")
    if deployed_hits:
        sys.exit(f"FAIL: deployed broker still denies this body: {deployed_hits}")
    print("OK: deployed broker accepts a body the pre-wq2292 copy refused")


if __name__ == "__main__":
    main()
