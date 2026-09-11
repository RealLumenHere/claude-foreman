"""wq#1814 (Phase-0 E.3): replay every raw stream-json capture this sandbox can read
through classifier.RunState and count what it actually sees.

Usage: python3 reports/api_retries_zero_rca_probe.py [classifier.py ...]
Defaults to the worktree's harness/classifier.py. Pass the pre-patch backup as a
second path to diff the two readings side by side.
"""
import collections
import glob
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURES = sorted(
    glob.glob("/home/lumen/agents/work-queue/_runs/*.jsonl")
    + glob.glob("/home/lumen/foreman/_runs/fbuild-*.jsonl"))


def load(path):
    # explicit loader: spec_from_file_location() returns None for a .bak suffix
    name = f"clf_{abs(hash(path))}"
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def frames(path):
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                f = json.loads(line)
            except ValueError:
                continue
            if isinstance(f, dict):
                yield f


def census():
    wire = collections.Counter()
    versions = collections.Counter()
    rle_status = collections.Counter()
    for p in CAPTURES:
        for f in frames(p):
            t, st = f.get("type"), f.get("subtype")
            if t == "system" and st == "init":
                versions[f.get("claude_code_version")] += 1
            if st == "api_retry" or t == "api_retry":
                wire[(t, st, f.get("error_status"), f.get("error"))] += 1
            if t == "rate_limit_event":
                info = f.get("rate_limit_info")
                rle_status[("nested" if info else "flat",
                            (info or f).get("status"))] += 1
    print(f"captures: {len(CAPTURES)}  cli versions (init frames): {dict(versions)}")
    print(f"api_retry frames (type, subtype, error_status, error): {dict(wire)}")
    print(f"rate_limit_event (shape, status): {dict(rle_status)}")


def replay(clf_path):
    mod = load(clf_path)
    total_retries, hits = 0, []
    for p in CAPTURES:
        st = mod.RunState()
        for f in frames(p):
            st.observe(f)
        total_retries += st.api_retries
        if st.api_retries or st.saw_limit:
            hits.append((os.path.basename(p), st.api_retries, st.saw_limit,
                         st.latest_resets_at, mod.classify(st)))
    print(f"\n[{clf_path}] total api_retries counted: {total_retries}")
    for h in hits:
        print("  file=%s api_retries=%d saw_limit=%s resets_at=%s verdict=%s" % h)


if __name__ == "__main__":
    census()
    for path in sys.argv[1:] or [os.path.join(HERE, "..", "harness", "classifier.py")]:
        replay(path)
