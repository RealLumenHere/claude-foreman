#!/usr/bin/env python3
"""CAL-1 calibration fixture (BUILD_PLAN.md §CAL-1, wq#619).

Scratch git repo + scratch wq DB + scratch events/runs/worktrees board, a reset, a
serial matrix runner that samples per-pool usage %-delta around each fworker run, and an
`apply` step that writes the measured usage into research/models.yaml.

Usage source: foreman_usaged's state.json (`limits.<pool>.percent`), which is the
/api/oauth/usage poll — this tool never touches ~/.claude/.credentials.json.

  cal1_fixture.py build --root DIR
  cal1_fixture.py reset --root DIR
  cal1_fixture.py sample [--state-file F]
  cal1_fixture.py run-matrix --root DIR --matrix sonnet-5@low,haiku-4-5 [--settle 90]
  cal1_fixture.py apply --results FILE --models-yaml research/models.yaml
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
FWORKER = os.path.join(HARNESS_DIR, "fworker.py")
WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
LIVE_STATE = "/home/lumen/foreman/state.json"
POOLS = ("session", "weekly_all", "fable_weekly_scoped")
TASK = "CAL-1 probe: append the line 'cal1 ok' to README.md and commit it"


def paths(root):
    return {
        "repo": os.path.join(root, "scratch_repo"),
        "wq_db": os.path.join(root, "scratch_wq.db"),
        "events": os.path.join(root, "events"),
        "runs": os.path.join(root, "_runs"),
        "worktrees": os.path.join(root, "worktrees"),
        "state": os.path.join(root, "state.json"),
        "results": os.path.join(root, "cal1_results.json"),
    }


def scratch_env(root, state_file=None):
    p = paths(root)
    env = dict(os.environ)
    env.update({
        "WQ_DB": p["wq_db"],
        "WQ_AGENT": "cal1-fixture",
        "FOREMAN_EVENTS_DIR": p["events"],
        "FOREMAN_RUNS_ROOT": p["runs"],
        "FOREMAN_WORKTREES_ROOT": p["worktrees"],
        "FOREMAN_STATE_FILE": state_file or p["state"],
        "FOREMAN_WQ_BIN": WQ_BIN,
        "FOREMAN_LUMEN_PR_BIN": "/bin/false",
        "FOREMAN_NTFY_BIN": "/bin/true",
    })
    return env


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def build(root):
    p = paths(root)
    for d in (root, p["events"], p["runs"], p["worktrees"]):
        os.makedirs(d, exist_ok=True)
    if not os.path.isdir(os.path.join(p["repo"], ".git")):
        os.makedirs(p["repo"], exist_ok=True)
        _git(p["repo"], "init", "-q", "-b", "main")
        _git(p["repo"], "config", "user.email", "cal1@lumen")
        _git(p["repo"], "config", "user.name", "cal1")
        with open(os.path.join(p["repo"], "README.md"), "w") as f:
            f.write("cal1 scratch\n")
        _git(p["repo"], "add", "README.md")
        _git(p["repo"], "commit", "-q", "-m", "init")
    return p


def reset(root):
    """Return the fixture to its just-built state: repo at the init commit, no worktrees,
    fresh wq DB, empty runs/events."""
    p = paths(root)
    if os.path.isdir(os.path.join(p["repo"], ".git")):
        subprocess.run(["git", "worktree", "prune"], cwd=p["repo"], capture_output=True)
        first = subprocess.run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=p["repo"],
                               capture_output=True, text=True, check=True).stdout.split()[0]
        _git(p["repo"], "checkout", "-q", "-f", "main")
        _git(p["repo"], "reset", "-q", "--hard", first)
        out = subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=p["repo"],
                             capture_output=True, text=True, check=True).stdout.split()
        for b in out:
            if b != "main":
                _git(p["repo"], "branch", "-q", "-D", b)
    for key in ("events", "runs", "worktrees"):
        shutil.rmtree(p[key], ignore_errors=True)
        os.makedirs(p[key], exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(p["wq_db"] + suffix)
        except FileNotFoundError:
            pass
    return p


def read_usage(state_file):
    """{pool: percent} from a foreman_usaged state.json; a missing pool is omitted."""
    with open(state_file) as f:
        limits = json.load(f).get("limits") or {}
    return {k: v["percent"] for k, v in limits.items() if isinstance(v, dict) and "percent" in v}


def usage_delta(before, after):
    return {k: round(after[k] - before[k], 3) for k in after if k in before}


def sum_tokens(obj):
    """Sum input/output token counters found anywhere in an attempt record (None if none)."""
    found = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("input_tokens", "output_tokens") and isinstance(v, (int, float)):
                    found.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return int(sum(found)) if found else None


def parse_cell(cell):
    model, _, effort = cell.partition("@")
    return model, (effort or None)


def run_cell(root, cell, usage_state, settle, claude_bin=None):
    """One matrix cell, fully isolated: reset -> sample -> fworker run -> settle -> sample."""
    p = reset(root)
    env = scratch_env(root)
    model, effort = parse_cell(cell)
    add = subprocess.run([WQ_BIN, "add", "--title", TASK], capture_output=True, text=True, env=env)
    if add.returncode != 0:
        raise RuntimeError("wq add failed: " + (add.stdout + add.stderr)[-300:])
    ticket = int(add.stdout.strip().split()[2])
    before = read_usage(usage_state)
    cmd = [sys.executable, FWORKER, "run", "--ticket", str(ticket), "--repo", p["repo"],
           "--model", model, "--archetype", "mechanical-edit",
           "--worker-id", f"cal1-wq{ticket}"]
    if effort:
        cmd += ["--effort", effort]
    if claude_bin:
        cmd += ["--claude-bin", claude_bin]
    t0 = time.time()
    run = subprocess.run(cmd, capture_output=True, text=True, env=env)
    elapsed = round(time.time() - t0, 1)
    time.sleep(settle)
    after = read_usage(usage_state)
    tokens = None
    attempt = os.path.join(p["runs"], f"wq{ticket}", "attempt-1.json")
    if os.path.isfile(attempt):
        with open(attempt) as f:
            tokens = sum_tokens(json.load(f))
    return {"cell": cell, "model": model, "effort": effort, "ticket": ticket,
            "exit_code": run.returncode, "elapsed_s": elapsed,
            "usage_before": before, "usage_after": after,
            "pct_delta": usage_delta(before, after), "tokens": tokens}


def run_matrix(root, cells, usage_state, settle, claude_bin=None):
    build(root)
    results = []
    for cell in cells:  # SERIAL on purpose: a parallel run would smear the account-global pct
        results.append(run_cell(root, cell, usage_state, settle, claude_bin))
        with open(paths(root)["results"], "w") as f:
            json.dump({"measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "results": results}, f, indent=2)
    return results


def render_measured(rows):
    """One `measured_usage:` flow-map line body for a model's rows (pct-delta per pool)."""
    parts = []
    for r in rows:
        deltas = ", ".join(f"{k}: {v:+g}" for k, v in sorted(r["pct_delta"].items()))
        tok = r["tokens"] if r["tokens"] is not None else "null"
        parts.append(f"{r['effort'] or 'none'}: {{pct_delta: {{{deltas}}}, tokens: {tok}, n: 1}}")
    return "{" + ", ".join(parts) + "}"


def apply_measured(results_file, models_yaml):
    """Insert `measured_usage:` under each measured model's `burn:` block (idempotent) and,
    when a tokens figure exists, flip that model's `measured_tokens: null` to the number."""
    with open(results_file) as f:
        results = json.load(f)["results"]
    by_model = {}
    for r in results:
        if r["exit_code"] == 0:
            by_model.setdefault(r["model"], []).append(r)
    with open(models_yaml) as f:
        lines = f.read().split("\n")
    out, cur, applied = [], None, []
    for line in lines:
        m = re.match(r"\s+- id: (\S+)", line)
        if m:
            cur = m.group(1)
        if cur in by_model and re.match(r"\s+measured_usage:", line):
            continue  # replace on re-apply
        if cur in by_model and re.match(r"\s+measured_tokens: null", line):
            toks = [r["tokens"] for r in by_model[cur] if r["tokens"] is not None]
            if toks:
                line = re.sub(r"null", str(int(sum(toks) / len(toks))), line, count=1)
        out.append(line)
        if cur in by_model and re.match(r"\s+measured_tokens:", line):
            indent = re.match(r"\s*", line).group(0)
            out.append(f"{indent}measured_usage: {render_measured(by_model[cur])}   # CAL-1 wq#619")
            applied.append(cur)
    with open(models_yaml, "w") as f:
        f.write("\n".join(out))
    return applied


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "reset"):
        s = sub.add_parser(name)
        s.add_argument("--root", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--state-file", default=LIVE_STATE)
    s = sub.add_parser("run-matrix")
    s.add_argument("--root", required=True)
    s.add_argument("--matrix", required=True, help="comma list of model[@effort]")
    s.add_argument("--usage-state", default=LIVE_STATE)
    s.add_argument("--settle", type=int, default=90, help="seconds to let usaged re-poll")
    s.add_argument("--claude-bin", default=None)
    s = sub.add_parser("apply")
    s.add_argument("--results", required=True)
    s.add_argument("--models-yaml", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        print(json.dumps(build(a.root)))
    elif a.cmd == "reset":
        print(json.dumps(reset(a.root)))
    elif a.cmd == "sample":
        print(json.dumps(read_usage(a.state_file)))
    elif a.cmd == "run-matrix":
        rs = run_matrix(a.root, a.matrix.split(","), a.usage_state, a.settle, a.claude_bin)
        print(json.dumps(rs, indent=2))
        return 0 if all(r["exit_code"] == 0 for r in rs) else 1
    elif a.cmd == "apply":
        print(json.dumps(apply_measured(a.results, a.models_yaml)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
