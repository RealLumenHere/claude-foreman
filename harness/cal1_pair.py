#!/usr/bin/env python3
"""CAL-1 paired haiku-vs-sonnet@low trial (wq#780 method, re-runnable for wq#3618).

Dispatches wq#780's four task templates once per model cell through the real
fworker.py, isolated the same way cal1_fixture.py isolates its matrix: a scratch wq DB,
scratch runs/events/worktrees, lumen-pr and ntfy shimmed off. The repo is a clone of
/home/lumen/foreman pinned to --rev with its remote removed, so nothing a worker does
can reach main. Each run is scored by a check command executed in the worker's
worktree here, not by the worker's own claim.

  cal1_pair.py run --root DIR --rev SHA --cells claude-haiku-5-5,claude-sonnet-5@low
  cal1_pair.py rescore --root DIR     # re-run checks on existing worktrees
  cal1_pair.py report --root DIR
"""
import argparse
import json
import os
import subprocess
import sys
import time

import cal1_fixture as fx

SOURCE_REPO = "/home/lumen/foreman"
MODELS_YAML = os.path.join(os.path.dirname(fx.HARNESS_DIR), "research", "models.yaml")
CLOSE = ("CAL-1 calibration run in a scratch clone with NO remote: implement it, run the "
         "relevant tests, commit on your branch. Do NOT run lumen-pr (there is nothing to "
         "push to). Close with `/home/lumen/bin/wq done <id> --no-artifact \"cal1 scratch "
         "clone\" --notes \"<test evidence>\"`.")

# wq#780's pairs A-D (research/CAL1_HAIKU_VS_SONNET_LOW.md), verbatim task + a check run
# in the resulting worktree. A check must exit 0 for the pair cell to score PASS.
TASKS = [
    {"key": "A", "archetype": "mechanical-edit",
     "title": "add a --version flag to harness/reaper.py",
     "check": [sys.executable, "harness/reaper.py", "--version"]},
    {"key": "B", "archetype": "mechanical-edit",
     "title": "add PEP 484 type hints to orchestrator/judgment.py",
     "check": ["bash", "-c", "git diff --quiet main -- orchestrator/judgment.py && exit 1; "
               "grep -q -- '->' orchestrator/judgment.py && "
               + sys.executable + " -m pytest -q orchestrator/tests"]},
    {"key": "C", "archetype": "probe-build",
     "title": "new read-only census script over queue.db (counts of tickets by lane and "
              "status, opened with mode=ro)",
     "check": ["bash", "-c", "f=$(git diff --name-only --diff-filter=A main | grep '\\.py$' "
               "| grep -v -e /tests/ -e '/test_' | head -1); test -n \"$f\" && grep -q 'mode=ro' \"$f\""]},
    {"key": "D", "archetype": "probe-build",
     "title": "new unit test file for make_worktree() branch naming in harness/fworker.py",
     "check": ["bash", "-c", "f=$(git diff --name-only --diff-filter=A main | grep 'test_.*\\.py$' "
               "| head -1); test -n \"$f\" && " + sys.executable + " -m pytest -q \"$f\""]},
]


def clone(root, rev):
    repo = os.path.join(root, "repo")
    if not os.path.isdir(os.path.join(repo, ".git")):
        subprocess.run(["git", "clone", "-q", SOURCE_REPO, repo], check=True)
        subprocess.run(["git", "-C", repo, "remote", "remove", "origin"], check=True)
    subprocess.run(["git", "-C", repo, "checkout", "-q", "-B", "main", rev], check=True)
    return repo


def wq_row(env, ticket):
    out = subprocess.run([fx.WQ_BIN, "show", str(ticket)], capture_output=True, text=True,
                         env=env).stdout
    pairs = (line.split(" : ", 1) for line in out.splitlines() if " : " in line)
    return {k.strip(): v.strip() for k, v in pairs}


def score(env, task, ticket, wt):
    check = subprocess.run(task["check"], cwd=wt, capture_output=True, text=True) \
        if os.path.isdir(wt) else None
    return {"wq_status": wq_row(env, ticket).get("status"),
            "check_ok": bool(check and check.returncode == 0),
            "check_tail": ((check.stdout + check.stderr)[-300:] if check else "no worktree")}


def attempt_record(env, ticket):
    d = os.path.join(env["FOREMAN_RUNS_ROOT"], f"wq{ticket}")
    names = sorted(n for n in os.listdir(d) if n.startswith("attempt-") and n.endswith(".json")) \
        if os.path.isdir(d) else []
    if not names:
        return {}
    with open(os.path.join(d, names[-1])) as f:
        return json.load(f)


def run_one(root, repo, task, cell):
    env = fx.scratch_env(root)
    # wq add refuses a model with no models.yaml row; trial the manifest that declares it
    env["FOREMAN_MODELS_YAML"] = MODELS_YAML
    model, effort = fx.parse_cell(cell)
    add = subprocess.run([fx.WQ_BIN, "add", "--title", task["title"], "--detail", CLOSE,
                          "--model", model, "--archetype", task["archetype"],
                          "--no-probe", "cal1 scratch"]
                         + (["--effort", effort] if effort else []),
                         capture_output=True, text=True, env=env)
    if add.returncode != 0:
        raise RuntimeError("wq add failed: " + (add.stdout + add.stderr)[-300:])
    ticket = int(add.stdout.strip().split()[2])
    cmd = [sys.executable, fx.FWORKER, "run", "--ticket", str(ticket), "--repo", repo,
           "--model", model, "--archetype", task["archetype"],
           "--worker-id", f"cal1pair-wq{ticket}"] + (["--effort", effort] if effort else [])
    t0 = time.time()
    run = subprocess.run(cmd, capture_output=True, text=True, env=env)
    elapsed = round(time.time() - t0, 1)
    rec = attempt_record(env, ticket)
    wt = rec.get("worktree") or os.path.join(env["FOREMAN_WORKTREES_ROOT"], f"wq{ticket}-a1")
    return {"pair": task["key"], "cell": cell, "ticket": ticket, "exit_code": run.returncode,
            "elapsed_s": elapsed, "classification": rec.get("classification"),
            "turns": rec.get("turns"), "cost_usd": rec.get("total_cost_usd"), "worktree": wt,
            **score(env, task, ticket, wt)}


def rescore(root):
    """Re-run every check (and re-read wq status) over an existing run's worktrees."""
    out = os.path.join(root, "pair_results.json")
    with open(out) as f:
        data = json.load(f)
    env = fx.scratch_env(root)
    tasks = {t["key"]: t for t in TASKS}
    for r in data["results"]:
        r.update(score(env, tasks[r["pair"]], r["ticket"], r["worktree"]))
    with open(out, "w") as f:
        json.dump(data, f, indent=2)


def run(root, rev, cells):
    os.makedirs(root, exist_ok=True)
    fx.reset(root)
    for d in ("events", "runs", "worktrees"):
        os.makedirs(fx.paths(root)[d], exist_ok=True)
    repo = clone(root, rev)
    out = os.path.join(root, "pair_results.json")
    results = []
    for task in TASKS:
        for cell in cells:  # serial, cells interleaved per pair so drift hits both arms
            results.append(run_one(root, repo, task, cell))
            with open(out, "w") as f:
                json.dump({"rev": rev, "cells": cells, "results": results}, f, indent=2)
    return results


def report(root):
    with open(os.path.join(root, "pair_results.json")) as f:
        data = json.load(f)
    print("| pair | cell | ticket | wq status | class | turns | wall s | cost $ | check |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in data["results"]:
        cost = f"{r['cost_usd']:.3f}" if isinstance(r["cost_usd"], (int, float)) else "-"
        print(f"| {r['pair']} | {r['cell']} | {r['ticket']} | {r['wq_status']} | "
              f"{r['classification']} | {r['turns']} | {r['elapsed_s']} | {cost} | "
              f"{'PASS' if r['check_ok'] else 'FAIL'} |")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--root", required=True)
    r.add_argument("--rev", required=True)
    r.add_argument("--cells", required=True)
    for name in ("rescore", "report"):
        sub.add_parser(name).add_argument("--root", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        run(a.root, a.rev, a.cells.split(","))
    elif a.cmd == "rescore":
        rescore(a.root)
    report(a.root)


if __name__ == "__main__":
    main()
