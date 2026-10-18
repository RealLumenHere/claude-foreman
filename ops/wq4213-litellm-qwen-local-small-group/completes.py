#!/usr/bin/env python3
"""wq#4213 — like wq#4135's probe_rung.py, but accepts a fallback hop.

probe_rung.py's answered_itself() requires attempted_fallbacks in (0, None),
which is right for verifying a replacement rung is alive on its OWN — but
wrong for verifying qwen-local-small post-fix, where answering via the new
`lumen-local-judge` fallback is the fix working as intended, not a defect.

  completes.py qwen-local-small
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wq4135-litellm-fast-fallback-retarget"))
import probe_rung  # noqa: E402


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("models", nargs="+")
    ap.add_argument("--timeout", type=int, default=90)
    a = ap.parse_args(argv)
    with open(probe_rung.KEY_PATH) as f:
        key = f.read().strip()
    results = [probe_rung.probe(m, key, a.timeout) for m in a.models]
    ok = True
    for r in results:
        completed = r["status"] == 200
        ok = ok and completed
        print({**r, "completed": completed})
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
