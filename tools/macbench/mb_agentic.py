"""Agentic score: a fixed suite of 8 small tasks run through the real fworker local
profile and the gateway, scored on success, turns, wall time, tool errors and whether
the resulting diff passes the task's own test.

Same suite, same prompts, same scoring for every model. Each task gets a fresh scratch
git repo; the task's test files are restored from the suite before scoring, so a model
cannot pass by editing the test.
"""
import json
import os
import shutil
import statistics
import subprocess
import sys
import time

import yaml

from mb_node import DISPATCH_CONFIG, REPO, Refused

if REPO not in sys.path:
    sys.path.insert(0, REPO)    # `harness.fworker` is the profile the score is meant to exercise

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE_DIR = os.path.join(HERE, "agentic", "suite")
SETTINGS = os.path.join(HERE, "agentic", "settings.json")
DEFAULT_CLAUDE = "/home/lumen/.npm-global/bin/claude"
TEST_TIMEOUT_S = 120
GIT = ["git", "-c", "user.name=macbench", "-c", "user.email=macbench@localhost"]


def load_suite(suite_dir=SUITE_DIR):
    tasks = []
    for name in sorted(os.listdir(suite_dir)):
        spec = os.path.join(suite_dir, name, "task.yaml")
        if os.path.exists(spec):
            with open(spec) as fh:
                t = yaml.safe_load(fh)
            t["dir"] = os.path.join(suite_dir, name)
            tasks.append(t)
    return tasks


def build_repo(task, root):
    """Fresh scratch git repo from the task's template. Returns its path."""
    repo = os.path.join(root, task["id"])
    if os.path.exists(repo):
        shutil.rmtree(repo)
    shutil.copytree(os.path.join(task["dir"], "repo"), repo,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    with open(os.path.join(repo, ".git", "info", "exclude"), "a") as fh:
        fh.write("__pycache__/\n*.pyc\n")     # running the tests must not count as a diff
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(GIT + ["commit", "-q", "-m", "baseline"], cwd=repo, check=True)
    return repo


def parse_stream(text):
    """Turns, tool calls and tool errors from claude's stream-json output."""
    s = {"turns": 0, "tool_calls": 0, "tool_errors": 0, "result_subtype": None,
         "is_error": None, "model_reported": None, "duration_ms": None}
    assistant = 0
    for ln in text.splitlines():
        try:
            f = json.loads(ln)
        except ValueError:
            continue
        kind = f.get("type")
        if kind == "system" and f.get("subtype") == "init":
            s["model_reported"] = f.get("model")
        elif kind == "assistant":
            assistant += 1
            for c in (f.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    s["tool_calls"] += 1
        elif kind == "user":
            for c in (f.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                    s["tool_errors"] += 1
        elif kind == "result":
            s["result_subtype"] = f.get("subtype")
            s["is_error"] = f.get("is_error")
            s["duration_ms"] = f.get("duration_ms")
            s["turns"] = f.get("num_turns") or 0
    s["turns"] = s["turns"] or assistant
    return s


def purge_bytecode(repo):
    """Drop every `__pycache__` under `repo`.

    CPython validates a `.pyc` against its source's mtime+size, so bytecode compiled by
    an earlier run - the model's own mid-session self-check, or this suite's baseline
    pass - is replayed whenever a later edit leaves those two unchanged (a same-second,
    same-length edit, which the mechanical-edit tasks invite by construction). That
    replays a stale pass or fail into the score. Purge before every scoring run.
    """
    for dirpath, dirnames, _ in os.walk(repo):
        if "__pycache__" in dirnames:
            shutil.rmtree(os.path.join(dirpath, "__pycache__"), ignore_errors=True)
            dirnames.remove("__pycache__")
        if ".git" in dirnames:
            dirnames.remove(".git")


def run_test(task, repo):
    """Restore protected test files from the suite, then run the task's test command."""
    for rel in task.get("protected", []):
        src = os.path.join(task["dir"], "repo", rel)
        dst = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    purge_bytecode(repo)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")   # and leave none behind
    try:
        p = subprocess.run(task["test_cmd"], shell=True, cwd=repo, capture_output=True,
                           text=True, timeout=TEST_TIMEOUT_S, env=env)
        return p.returncode == 0, (p.stdout + p.stderr)[-400:]
    except subprocess.TimeoutExpired:
        return False, "test timed out"


def diff_stats(repo, protected):
    """Files and non-protected lines changed against the baseline commit.

    Staged into a scratch index built from scratch, so the comparison is HEAD's tree
    against freshly hashed content. The repo's own index carries the stat data recorded
    at baseline commit time, and git skips reading a file whose size and mtime still
    match it - which a mtime-preserving write leaves true even when the bytes differ,
    scoring a real edit as no diff at all (same stale-stat trap as `purge_bytecode`).
    """
    index = os.path.join(repo, ".git", "macbench-diff-index")
    if os.path.exists(index):
        os.remove(index)
    env = dict(os.environ, GIT_INDEX_FILE=index)
    subprocess.run(["git", "add", "-A"], cwd=repo, env=env, capture_output=True)
    p = subprocess.run(["git", "diff", "--cached", "--numstat", "HEAD"], cwd=repo, env=env,
                       capture_output=True, text=True)
    files = lines = 0
    for ln in p.stdout.splitlines():
        a, d, name = (ln.split("\t") + ["", "", ""])[:3]
        if name in protected:
            continue
        files += 1
        lines += (int(a) if a.isdigit() else 0) + (int(d) if d.isdigit() else 0)
    return files, lines


def gateway_env(model, base_url, token):
    """Child env for a local-profile worker pointed at a gateway - the same variables
    fworker.build_env sets for a local-pool model, minus ticket identity."""
    from harness import fworker
    env = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
              "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"):
        env.pop(k, None)
    env["ANTHROPIC_BASE_URL"] = base_url
    env["ANTHROPIC_AUTH_TOKEN"] = token
    env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
    env.update(fworker.local_profile_env())
    return env


def claude_cmd(task, model, claude_bin):
    from harness import fworker
    return fworker.build_claude_cmd(task["prompt"], model, None, SETTINGS, [],
                                    max_turns=task.get("max_turns", 30),
                                    claude_bin=claude_bin, local_profile=True)


def run_task(task, model, root, base_url, token, claude_bin=DEFAULT_CLAUDE, timeout_s=1200):
    repo = build_repo(task, root)
    t0 = time.time()
    timed_out = False
    try:
        p = subprocess.run(claude_cmd(task, model, claude_bin), cwd=repo, capture_output=True,
                           text=True, timeout=timeout_s, env=gateway_env(model, base_url, token))
        out = p.stdout
    except subprocess.TimeoutExpired as e:
        timed_out = True
        raw = e.stdout or b""
        out = raw.decode(errors="replace") if isinstance(raw, bytes) else raw
    wall = time.time() - t0
    s = parse_stream(out)
    passed, tail = run_test(task, repo)
    files, lines = diff_stats(repo, set(task.get("protected", [])))
    ok = passed and files > 0 and not timed_out
    return {"phase": "agentic_task", "task": task["id"], "kind": task["kind"], "success": ok,
            "test_pass": passed, "wall_s": round(wall, 1), "timed_out": timed_out,
            "diff_files": files, "diff_lines": lines, "test_tail": tail, **s}


def summarize(records):
    n = len(records)
    ok = sum(1 for r in records if r["success"])
    by_kind = {}
    for r in records:
        by_kind.setdefault(r["kind"], []).append(r["success"])
    return {"phase": "agentic_summary", "tasks": n, "successes": ok,
            "agentic_score": round(ok / n, 3) if n else None,
            "success_by_kind": {k: f"{sum(v)}/{len(v)}" for k, v in by_kind.items()},
            "turns_median": statistics.median(r["turns"] for r in records) if n else None,
            "wall_total_s": round(sum(r["wall_s"] for r in records), 1),
            "tool_calls": sum(r["tool_calls"] for r in records),
            "tool_errors": sum(r["tool_errors"] for r in records)}


def guard_model(model, allow_serving_group, config_path=DISPATCH_CONFIG):
    """A model alias that is a local-worker group member is real capacity: opt-in only."""
    with open(config_path) as fh:
        group = set(json.load(fh)["local_backends"].get("local-worker", {}).get("group", []))
    if (model in group or model == "local-worker") and not allow_serving_group:
        raise Refused(f"{model} serves the local-worker group; pass --allow-serving-group")


def run_suite(model, base_url, token, sink, tags, root, claude_bin=DEFAULT_CLAUDE,
              timeout_s=1200, tasks=None):
    records = []
    for task in tasks or load_suite():
        r = run_task(task, model, root, base_url, token, claude_bin, timeout_s)
        r.update(tags)
        sink(r)
        records.append(r)
    s = summarize(records)
    s.update(tags)
    sink(s)
    return records, s
