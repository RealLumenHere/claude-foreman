"""wq#5009 end to end on the real fworker dispatch path (scratch repo + scratch wq DB +
tests/fake_claude.py): a worker parks on a decision card, the slot is freed with nothing
running, Frank answers, the SAME session resumes with the answer and finishes."""
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_fworker_integration import (  # noqa: E402
    make_scratch_repo, make_scratch_env, wq_add, wq_status, wq_show_field, run_fworker, WQ_BIN)
import fworker  # noqa: E402
import park  # noqa: E402

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setup(tmp_path, scenario, extra=None):
    repo = make_scratch_repo(tmp_path)
    notices = str(tmp_path / "notices.log")
    ntfy = tmp_path / "ntfy-stub"
    ntfy.write_text("#!/bin/sh\nprintf 'CALL %s\\n' \"$4\" >> " + notices + "\n")
    ntfy.chmod(0o755)
    env, _db, _ev, runs_root, worktrees_root, _st = make_scratch_env(tmp_path, 0, scenario, extra=dict({
        "FAKE_CLAUDE_SPAWN_LOG": str(tmp_path / "spawns.log"),
        "FAKE_CLAUDE_PROMPT_LOG": str(tmp_path / "prompts.log"),
        "FOREMAN_NTFY_BIN": str(ntfy),
        "FOREMAN_JUDGE_SHADOW": "0",          # `wq decide` must not launch a real judge job
        "FOREMAN_NO_VERIFY_PASS": "1",
    }, **(extra or {})))
    tid = wq_add(env, f"park {scenario}")
    env["FAKE_CLAUDE_TICKET"] = str(tid)
    return repo, env, tid, runs_root, tmp_path


def spawns(tmp_path):
    p = tmp_path / "spawns.log"
    return p.read_text().split() if p.exists() else []


def prompts(tmp_path):
    p = tmp_path / "prompts.log"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def park_cli(env, *args):
    return subprocess.run([sys.executable, os.path.join(HARNESS, "park.py"), *map(str, args)],
                          capture_output=True, text=True, env=env)


def rec(runs_root, tid):
    return json.load(open(os.path.join(runs_root, f"wq{tid}", "park.json")))


def attempts(runs_root, tid):
    return sorted(glob.glob(os.path.join(runs_root, f"wq{tid}", "attempt-*.json")))


def test_park_frees_the_slot_then_the_same_session_resumes_with_the_answer(tmp_path):
    repo, env, tid, runs_root, tmp = setup(tmp_path, "park_then_finish")

    r1 = run_fworker(env, tid, repo, "park_then_finish")
    assert r1.returncode == fworker.EXIT_PARKED, r1.stdout + r1.stderr
    assert "PARKED" in r1.stdout
    assert wq_status(env, tid) == "pending_review"
    assert wq_show_field(env, tid, "claimed_by") in ("None", "", None)
    assert "Rotate now or after the freeze?" in subprocess.run(
        [WQ_BIN, "show", str(tid), "--json"], capture_output=True, text=True, env=env).stdout
    parked = rec(runs_root, tid)
    assert parked["state"] == "PARKED" and parked["session_id"]
    assert attempts(runs_root, tid) == []                 # uncharged: no attempt record
    assert spawns(tmp) == ["fresh"]
    assert (tmp / "notices.log").read_text().count("CALL DECISION") == 1
    # nothing is running for this ticket while it is parked
    assert subprocess.run(["pgrep", "-f", str(tmp)], capture_output=True).returncode != 0

    # a dispatch while parked cannot take it
    r_mid = run_fworker(env, tid, repo, "park_then_finish")
    assert r_mid.returncode == fworker.EXIT_CLAIM_ERROR and spawns(tmp) == ["fresh"]

    a = park_cli(env, "answer", tid, "2")
    assert a.returncode == 0, a.stdout + a.stderr
    assert wq_status(env, tid) == "open"

    r2 = run_fworker(env, tid, repo, "park_then_finish")
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert wq_status(env, tid) == "done"
    assert spawns(tmp) == ["fresh", "resume"]
    resumed = prompts(tmp)[-1]
    assert resumed["resume"] is True and "Frank chose: after freeze" in resumed["prompt"]
    assert "park-and-resume round 1" in r2.stderr + r2.stdout
    assert rec(runs_root, tid)["state"] == "RESUMED"
    records = [json.load(open(p)) for p in attempts(runs_root, tid)]
    assert [x["attempt"] for x in records] == [1]         # park + resume = one attempt
    assert records[0]["session_id"] == parked["session_id"]
    assert records[0]["classification"] == "SUCCESS"


def test_dead_session_falls_back_to_a_fresh_one_that_knows_the_decision(tmp_path):
    repo, env, tid, runs_root, tmp = setup(tmp_path, "park_then_finish")
    assert run_fworker(env, tid, repo, "park_then_finish").returncode == fworker.EXIT_PARKED
    assert park_cli(env, "answer", tid, "rotate now").returncode == 0
    env["FAKE_CLAUDE_RESUME_GONE"] = "1"
    r = run_fworker(env, tid, repo, "park_then_finish")
    assert r.returncode == 0, r.stdout + r.stderr
    assert spawns(tmp) == ["fresh", "resume-gone", "fresh"]
    assert "could not be resumed" in r.stderr + r.stdout
    last = prompts(tmp)[-1]
    assert last["resume"] is False and "DECISION ALREADY MADE" in last["prompt"]
    assert "Frank's answer: rotate now" in last["prompt"]
    assert wq_status(env, tid) == "done"


def test_malformed_card_gets_one_rewrite_then_parks(tmp_path):
    repo, env, tid, runs_root, tmp = setup(tmp_path, "park_bad_card_then_good")
    r = run_fworker(env, tid, repo, "park_bad_card_then_good")
    assert r.returncode == fworker.EXIT_PARKED, r.stdout + r.stderr
    assert spawns(tmp) == ["fresh", "resume"]
    assert "PARK CARD REJECTED" in prompts(tmp)[-1]["prompt"]
    assert "`ask.text` is required" in prompts(tmp)[-1]["prompt"]
    assert rec(runs_root, tid)["state"] == "PARKED"


def test_hop_limit_stops_a_worker_that_only_ever_parks(tmp_path):
    repo, env, tid, runs_root, tmp = setup(tmp_path, "park_forever",
                                           extra={"FOREMAN_PARK_MAX_HOPS": "2"})
    for _ in range(2):
        assert run_fworker(env, tid, repo, "park_forever").returncode == fworker.EXIT_PARKED
        assert park_cli(env, "answer", tid, "1").returncode == 0
    r = run_fworker(env, tid, repo, "park_forever")
    assert r.returncode != fworker.EXIT_PARKED
    assert "hop limit" in r.stderr + r.stdout
    assert rec(runs_root, tid)["hops"] == 2
    assert wq_status(env, tid) != "pending_review"        # not parked a third time


def test_park_off_is_the_old_behaviour(tmp_path):
    repo, env, tid, runs_root, tmp = setup(tmp_path, "park_then_finish",
                                           extra={"FOREMAN_PARK": "off"})
    r = run_fworker(env, tid, repo, "park_then_finish")
    assert r.returncode != fworker.EXIT_PARKED
    assert not os.path.exists(os.path.join(runs_root, f"wq{tid}", "park.json"))
    assert wq_status(env, tid) != "pending_review"


def test_exit_code_is_unique_and_refill_covers_it():
    codes = [v for k, v in vars(fworker).items() if k.startswith("EXIT_") and isinstance(v, int)]
    assert codes.count(fworker.EXIT_PARKED) == 1
    src = open(fworker.__file__).read()
    assert 'EXIT_PARKED: "parked"' in src
