"""wq#5144 (wq#5041 gap 6): the worker stop-code contract. Workers end with
`STOP: <0-4>`; fworker routes 1/2/3/4 on that number instead of inferring from prose,
and 0 is still verified like any done claim."""
import glob
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stop_code  # noqa: E402
import park  # noqa: E402
import failure_record  # noqa: E402
from test_fworker_integration import (  # noqa: E402
    make_scratch_repo, make_scratch_env, wq_add, wq_status, wq_show_field, run_fworker,
    WQ_BIN)
import fworker  # noqa: E402

HARNESS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- parsing

@pytest.mark.parametrize("text,code", [
    ("done\nSTOP: 0\nEXIT_SIGNAL: true\nTICKET_CLOSED", 0),
    ("x\nSTOP:1\nEXIT_SIGNAL: false\nBLOCKED", 1),
    ("x\n`STOP: 2`\nEXIT_SIGNAL: false", 2),
    ("x\n- **STOP = 3**", 3),
    ("STOP: 2\nlater\nSTOP: 4\nPARKED", 4),              # last one wins
    ("we must STOP: 1 now, mid-sentence", None),        # a line of its own only
    ("no code at all\nEXIT_SIGNAL: true", None),
    (None, None),
])
def test_parse(text, code):
    assert stop_code.parse(text)[0] == code


def test_out_of_range_is_not_a_code_but_stays_visible():
    assert stop_code.parse("STOP: 7") == (None, "7")
    rec = stop_code.record("x\nSTOP: 7", enforced=True)
    assert rec["code"] is None and rec["raw"] == "7" and rec["route"] is None


def test_every_code_has_a_name_and_a_route():
    assert set(stop_code.NAMES) == set(stop_code.ROUTES) == {0, 1, 2, 3, 4}
    assert stop_code.ROUTES[stop_code.NEEDS_ANSWER] == "park"


def test_contradictions_are_named():
    c = stop_code.contradictions(0, False, "x\nSTOP: 0\nEXIT_SIGNAL: false\nBLOCKED")
    assert any("EXIT_SIGNAL: false" in x for x in c) and any("BLOCKED" in x for x in c)
    assert stop_code.contradictions(4, False, "STOP: 4\nEXIT_SIGNAL: false\nPARKED") == \
        ["STOP: 4 without a foreman-park card"]
    assert stop_code.contradictions(
        0, True, "ok\nSTOP: 0\nEXIT_SIGNAL: true\nTICKET_CLOSED") == []


def test_record_routes_only_when_enforced():
    t = "x\nSTOP: 1\nEXIT_SIGNAL: false\nBLOCKED"
    assert stop_code.record(t, exit_signal=False, enforced=True)["route"] == "block"
    assert stop_code.record(t, exit_signal=False, enforced=False)["route"] is None


def test_summary_line_skips_the_closing_lines():
    t = "\n**the device is gone**\nSTOP: 1\nEXIT_SIGNAL: false\nBLOCKED"
    assert stop_code.summary_line(t) == "the device is gone"
    assert stop_code.block_reason("STOP: 1\nEXIT_SIGNAL: false").endswith(
        "no reason given in the final message")


def test_mode(monkeypatch):
    monkeypatch.delenv(stop_code.MODE_ENV, raising=False)
    assert stop_code.mode() == "enforce"
    for m in ("shadow", "off", "OFF "):
        monkeypatch.setenv(stop_code.MODE_ENV, m)
        assert stop_code.mode() == m.strip().lower()
    monkeypatch.setenv(stop_code.MODE_ENV, "bogus")
    assert stop_code.mode() == "enforce"


def test_worker_prompt_carries_the_contract():
    text = open(os.path.join(HARNESS, "prompts", "worker.md")).read()
    assert stop_code.PROTOCOL_MARKER in text
    for n in range(5):
        assert f"STOP: {n}" in text
    assert "`STOP: 4`, `EXIT_SIGNAL: false` and `PARKED`" in text
    assert "STOP: 4" in park.rewrite_text("bad card")
    assert "STOP: 4" in stop_code.card_missing_text()


def test_worker_limit_triages_as_limit():
    v, head = failure_record.triage("WORKER_LIMIT", None, [], [], None, [])
    assert v == failure_record.TRIAGE_LIMIT
    assert fworker.classify_block_reason("WORKER_LIMIT") == fworker.BLOCK_LIMIT_HIT


# ---------------------------------------------------------------- in-session Stop hook

sys.path.insert(0, os.path.join(HARNESS, "hooks"))
import stop_gate  # noqa: E402
from test_wq5007_exit_gate import hook_env, hook  # noqa: E402,F401 -- fixture + helper


@pytest.mark.parametrize("text,allowed", [
    ("context nearly full.\nSTOP: 3\nEXIT_SIGNAL: false", True),
    ("need Frank's pick.\nSTOP: 4\nEXIT_SIGNAL: false\nPARKED", True),
    ("half way.\nSTOP: 2\nEXIT_SIGNAL: false", False),   # unfinished: keep working
    ("pausing here.\nEXIT_SIGNAL: false", False),         # unchanged wq#5007 behaviour
])
def test_stop_hook_steps_aside_for_limit_and_needs_answer_only(tmp_path, hook_env, text,
                                                                allowed):
    hook_env.update(done=False, status="claimed")
    code, _ = hook(tmp_path, text)
    assert (code == stop_gate.ALLOW) is allowed


def test_stop_hook_ignores_codes_when_stop_code_is_off(tmp_path, hook_env, monkeypatch):
    monkeypatch.setenv(stop_code.MODE_ENV, "off")
    hook_env.update(done=False, status="claimed")
    assert hook(tmp_path, "full.\nSTOP: 3\nEXIT_SIGNAL: false")[0] == stop_gate.BLOCK


# ---------------------------------------------------------------- end to end (real fworker)

def _run(tmp_path, scenario, extra=None):
    repo = make_scratch_repo(tmp_path)
    env, _db, _ev, runs_root, _wt, _st = make_scratch_env(tmp_path, 0, scenario, extra=dict({
        "FAKE_CLAUDE_SPAWN_LOG": str(tmp_path / "spawns.log"),
        "FOREMAN_JUDGE_SHADOW": "0",
        "FOREMAN_NO_VERIFY_PASS": "1",
    }, **(extra or {})))
    tid = wq_add(env, f"stop code {scenario}")
    env["FAKE_CLAUDE_TICKET"] = str(tid)
    r = run_fworker(env, tid, repo, scenario, timeout=120)
    sp = tmp_path / "spawns.log"
    spawns = sp.read_text().split() if sp.exists() else []
    records = [json.load(open(p)) for p in
               sorted(glob.glob(os.path.join(runs_root, f"wq{tid}", "attempt-*.json")))]
    return r, env, tid, spawns, records


def test_stop_1_blocks_on_the_code_with_no_retry(tmp_path):
    r, env, tid, spawns, records = _run(tmp_path, "stop_cant_run")
    assert r.returncode == fworker.EXIT_BLOCKED, r.stdout + r.stderr
    assert spawns == ["fresh"]                                # no retry, no nudge
    assert wq_status(env, tid) == "blocked"
    shown = subprocess.run([WQ_BIN, "show", str(tid), "--json"], capture_output=True,
                           text=True, env=env).stdout
    assert "worker STOP: 1 (cannot run, wq#5144): the cited device is gone" in shown
    assert records[-1]["classification"] == "BLOCKED"
    sc = records[-1]["stop_code"]
    assert sc["code"] == 1 and sc["route"] == "block" and sc["enforced"] is True


def test_stop_2_resumes_and_is_not_labelled_a_false_complete(tmp_path):
    r, env, tid, spawns, records = _run(tmp_path, "stop_unfinished_then_done")
    assert r.returncode == 0, r.stdout + r.stderr
    assert spawns == ["fresh", "resume"]
    assert wq_status(env, tid) == "done"
    assert [x["classification"] for x in records] == ["UNFINISHED", "SUCCESS"]
    assert records[0]["stop_code"]["route"] == "resume"
    assert records[1]["stop_code"]["code"] == 0


def test_stop_3_takes_the_limit_path(tmp_path):
    r, env, tid, spawns, records = _run(tmp_path, "stop_limit")
    assert r.returncode == fworker.EXIT_BLOCKED, r.stdout + r.stderr
    assert len(spawns) == fworker.MAX_ATTEMPTS
    assert {x["classification"] for x in records} == {"WORKER_LIMIT"}
    assert wq_status(env, tid) == "blocked"
    assert wq_show_field(env, tid, "block_reason").startswith("LIMIT:")


def test_stop_4_without_a_card_asks_once_then_goes_to_frank(tmp_path):
    r, env, tid, spawns, records = _run(tmp_path, "stop_needs_answer_no_card")
    assert r.returncode == fworker.EXIT_FRANK_GATED, r.stdout + r.stderr
    assert spawns == ["fresh", "resume"]                      # exactly one card request
    assert wq_status(env, tid) in ("frank_gated", "pending_review")
    assert wq_show_field(env, tid, "claimed_by") in ("None", "", None)
    assert records == []                                      # uncharged, like a park
    shown = subprocess.run([WQ_BIN, "show", str(tid), "--json"], capture_output=True,
                           text=True, env=env).stdout
    assert "need Frank to pick the vendor" in shown


def test_stop_4_that_sends_the_card_when_asked_parks(tmp_path):
    r, env, tid, spawns, records = _run(tmp_path, "stop_needs_answer_card_on_ask")
    assert r.returncode == fworker.EXIT_PARKED, r.stdout + r.stderr
    assert spawns == ["fresh", "resume"]
    assert wq_status(env, tid) == "pending_review"


def test_shadow_mode_records_but_does_not_route(tmp_path):
    r, env, tid, spawns, records = _run(tmp_path, "stop_cant_run",
                                        {"FOREMAN_STOP_CODE": "shadow"})
    sc = records[0]["stop_code"]
    assert sc["code"] == 1 and sc["enforced"] is False and sc["route"] is None
    assert records[0]["classification"] != "BLOCKED"          # inference, not the code
    assert "STOP: 1 (cannot run" not in (wq_show_field(env, tid, "block_reason") or "")
