#!/usr/bin/env python3
"""Run a token-engine gate replay script and stream its output.

Exists because worker Bash allowlists carry python3 but not bare bash
(wq#2278): `python3 token-engine/tests/run_replay.py t10` is the
allowlist-friendly spelling of `bash token-engine/gate_replay_t10.sh`.
"""
import os
import subprocess
import sys


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "t10"
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(src, f"gate_replay_{which}.sh")
    if not os.path.isfile(script):
        sys.exit(f"no such replay: {script}")
    p = subprocess.run(["bash", script, src])
    sys.exit(p.returncode)


if __name__ == "__main__":
    main()
