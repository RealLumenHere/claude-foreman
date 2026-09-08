#!/usr/bin/env python3
"""run_sweep.py — the wq#1492 one-time backlog sweep, pinned to the LIVE runtime.

    python3 ops/wq1492-liveness-backlog-sweep/run_sweep.py dryrun
    python3 ops/wq1492-liveness-backlog-sweep/run_sweep.py apply     # --write-bindings --apply
    python3 ops/wq1492-liveness-backlog-sweep/run_sweep.py apply2    # idempotence check

Writes <RUNS>/<name>.json (the sweep's --json records) and <name>.log (the human
table). Runtime (state + audit) is the LIVE /home/lumen/foreman/harness/runtime so
UNKNOWN dedupe and the audit trail are shared with the timer; the ledger is THIS
checkout's (the staged wq#1492 bindings) until the PR lands on the primary tree.
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
RUNS = os.environ.get("FOREMAN_RUNS", "/home/lumen/foreman/_runs/wq1492")
LIVE_RUNTIME = "/home/lumen/foreman/harness/runtime"

os.environ.setdefault("FOREMAN_LIVENESS_RUNTIME", LIVE_RUNTIME)
os.environ["WQ_AGENT"] = "liveness-clear"
sys.path.insert(0, os.path.join(REPO, "harness"))
import liveness_clear  # noqa: E402


def main(argv):
    name = argv[1] if len(argv) > 1 else "dryrun"
    apply = name.startswith("apply")
    os.makedirs(RUNS, exist_ok=True)
    args = ["--scope", "all", "--agent", "liveness-clear", "--ledger",
            os.path.join(REPO, "harness", "liveness_ledger.yaml")]
    if apply:
        args += ["--apply", "--write-bindings"]
    buf = io.StringIO()
    real = sys.stdout
    sys.stdout = buf
    try:
        liveness_clear.main(args + ["--json"])
    finally:
        sys.stdout = real
    recs = json.loads(buf.getvalue())
    with open(os.path.join(RUNS, f"{name}.json"), "w") as f:
        json.dump(recs, f, indent=1)
    counts, actions = {}, {}
    for r in recs:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        actions[r["action"]] = actions.get(r["action"], 0) + 1
    lines = [f"{name}: {len(recs)} backlog ticket(s) [scope=all] apply={apply} "
             + " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
             + " | actions " + " ".join(f"{k}={v}" for k, v in sorted(actions.items()))]
    for r in recs:
        if r["verdict"] == "NO_TAG":
            continue
        lines.append(f"  wq#{r['ticket']:<5} {r['verdict']:<13} {r['action']:<12} [{r['lane']}/{r['prior_status']}] {r.get('close', '')}")
        for p in r["probes"]:
            lines.append(f"           {p['verdict']:<13} {p['probe']}({p['args']}) -> {p['detail']}")
    text = "\n".join(lines)
    with open(os.path.join(RUNS, f"{name}.log"), "w") as f:
        f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
