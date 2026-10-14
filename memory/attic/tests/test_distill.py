"""distill.py unit tests (wq#1183) — fact validation, secret scrubbing, note
format (the recall contract), git best-effort, proposal-only ingests, and the
end-to-end CLI with a monkeypatched chat(). Zero quota."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import distill  # noqa: E402
import episodic  # noqa: E402
import recall  # noqa: E402


def _facts_json(items):
    return json.dumps(items)


# --- parse_facts -----------------------------------------------------------

def test_parse_valid_facts():
    raw = _facts_json([
        {"fact": "thor mounts /srv over NFS, writes there are slow",
         "scope": "fleet", "confidence": 0.9, "state": None},
        {"fact": "harness tests run with pytest from harness/",
         "scope": "repo:claude-foreman", "confidence": 0.8,
         "state": {"subject": "claude-foreman", "predicate": "test_runner",
                   "value": "pytest"}},
    ])
    facts = distill.parse_facts(raw)
    assert len(facts) == 2
    assert facts[0]["scope"] == "fleet"
    assert facts[1]["state"]["predicate"] == "test_runner"


def test_parse_tolerates_prose_and_fences():
    raw = "Here you go:\n```json\n" + _facts_json(
        [{"fact": "x is true", "scope": "fleet", "confidence": 0.5}]) + "\n```"
    assert len(distill.parse_facts(raw)) == 1


def test_parse_garbage_yields_empty():
    assert distill.parse_facts("no json here") == []
    assert distill.parse_facts('{"fact": "an object, not array"}') == []
    assert distill.parse_facts(None) == []
    assert distill.parse_facts("[not valid") == []


def test_parse_caps_at_max_facts():
    raw = _facts_json([{"fact": f"fact {i}", "scope": "fleet"} for i in range(9)])
    assert len(distill.parse_facts(raw)) == distill.MAX_FACTS


def test_parse_drops_secretlike_facts():
    raw = _facts_json([
        {"fact": "the key is sk-" + "abc123def456ghi789", "scope": "fleet"},
        {"fact": "auth uses Bearer " + "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
         "scope": "fleet"},
        {"fact": "api_key= lives in the env file", "scope": "fleet"},
        {"fact": "the dispatcher polls every 60s", "scope": "fleet"},
    ])
    facts = distill.parse_facts(raw)
    assert [f["fact"] for f in facts] == ["the dispatcher polls every 60s"]


def test_parse_invalid_scope_falls_back_to_fleet():
    raw = _facts_json([{"fact": "x", "scope": "galaxy:andromeda"}])
    assert distill.parse_facts(raw)[0]["scope"] == "fleet"


def test_parse_invalid_state_triple_dropped_fact_kept():
    raw = _facts_json([{
        "fact": "node kepler runs the gateway", "scope": "fleet",
        "state": {"subject": "bad subject!", "predicate": "role", "value": "gw"}}])
    facts = distill.parse_facts(raw)
    assert len(facts) == 1 and facts[0]["state"] is None


def test_parse_secretlike_state_value_dropped():
    raw = _facts_json([{
        "fact": "gateway has a token", "scope": "fleet",
        "state": {"subject": "gw", "predicate": "token",
                  "value": "ghp_abcdefghijklmnopqrstuv123456"}}])
    assert distill.parse_facts(raw)[0]["state"] is None


def test_parse_confidence_clamped():
    raw = _facts_json([{"fact": "x", "scope": "fleet", "confidence": 7},
                       {"fact": "y", "scope": "fleet", "confidence": "bad"}])
    facts = distill.parse_facts(raw)
    assert facts[0]["confidence"] == 1.0
    assert facts[1]["confidence"] == 0.5


# --- note writing / recall contract ---------------------------------------

META = {"extracted_at": "2026-09-02T00:00:00Z", "model": "test", "attempts": 1}


def test_write_note_and_recall_roundtrip(tmp_path):
    facts = [
        {"fact": "dispatcher polls every 60s", "scope": "fleet",
         "confidence": 0.9, "state": None},
        {"fact": "tests use pytest", "scope": "repo:claude-foreman",
         "confidence": 0.8,
         "state": {"subject": "claude-foreman", "predicate": "test_runner",
                   "value": "pytest"}},
    ]
    path = distill.write_note(55, facts, META, store=str(tmp_path))
    assert path.endswith("2026-09-02-wq55.md")
    got = recall.top_facts(store=str(tmp_path))
    assert ("fleet", "dispatcher polls every 60s") in got
    assert ("repo:claude-foreman", "tests use pytest") in got


def test_write_note_no_git_repo_is_fine(tmp_path):
    facts = [{"fact": "x", "scope": "fleet", "confidence": 0.5, "state": None}]
    path = distill.write_note(1, facts, META, store=str(tmp_path))
    assert os.path.isfile(path)
    assert not distill.git_commit(str(tmp_path), "msg")


def test_git_commit_when_repo_exists(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    facts = [{"fact": "x", "scope": "fleet", "confidence": 0.5, "state": None}]
    distill.write_note(2, facts, META, store=str(tmp_path))
    p = subprocess.run(["git", "-C", str(tmp_path), "log", "--oneline"],
                       capture_output=True, text=True)
    assert "distill: wq#2" in p.stdout


def test_ingest_argvs_only_for_state_facts():
    facts = [
        {"fact": "a", "scope": "fleet", "confidence": 0.5, "state": None},
        {"fact": "b", "scope": "fleet", "confidence": 0.5,
         "state": {"subject": "gw", "predicate": "role", "value": "gateway"}},
    ]
    argvs = distill.ingest_argvs(facts)
    assert len(argvs) == 1
    assert argvs[0][2:6] == ["ingest", "gw", "role", "gateway"]
    assert "--source" in argvs[0]


# --- CLI end-to-end with fake chat ----------------------------------------

def _seed_episode(tmp_runs, ticket=77):
    episodic.append_event(ticket, {
        "type": "attempt_end", "attempt": 1, "classification": "SUCCESS",
        "result_text": "landed PR", "assistant_tail": ["did the thing"]},
        runs_root=str(tmp_runs))


def test_main_writes_note_and_proposes_ingests(tmp_path, monkeypatch, capsys):
    runs = tmp_path / "runs"
    store = tmp_path / "store"
    _seed_episode(runs)
    monkeypatch.setattr(distill, "chat", lambda *a, **k: _facts_json([
        {"fact": "workers land via lumen-pr", "scope": "fleet",
         "confidence": 0.9,
         "state": {"subject": "fleet", "predicate": "landing_path",
                   "value": "lumen-pr"}}]))
    rc = distill.main(["--ticket", "77", "--runs-root", str(runs),
                       "--store", str(store)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "-wq77.md" in out
    assert "proposed: " in out and "ingested" not in out
    assert (store / "facts").is_dir()


def test_main_ingest_flag_runs_state_cli(tmp_path, monkeypatch, capsys):
    runs = tmp_path / "runs"
    _seed_episode(runs, ticket=78)
    monkeypatch.setattr(distill, "chat", lambda *a, **k: _facts_json([
        {"fact": "x", "scope": "fleet", "confidence": 0.9,
         "state": {"subject": "s", "predicate": "p", "value": "v"}}]))
    calls = []
    monkeypatch.setattr(distill.subprocess, "run",
                        lambda argv, **k: calls.append(argv) or
                        type("P", (), {"returncode": 0, "stdout": ""})())
    rc = distill.main(["--ticket", "78", "--runs-root", str(runs),
                       "--store", str(tmp_path / "store"), "--ingest"])
    assert rc == 0
    ingest_calls = [c for c in calls if "ingest" in c]
    assert len(ingest_calls) == 1
    assert "ingested (0):" in capsys.readouterr().out


def test_main_no_episode_exits_1(tmp_path, capsys):
    rc = distill.main(["--ticket", "999", "--runs-root", str(tmp_path),
                       "--store", str(tmp_path / "store")])
    assert rc == 1
    assert "no episode" in capsys.readouterr().out


def test_main_zero_facts_writes_nothing(tmp_path, monkeypatch, capsys):
    runs = tmp_path / "runs"
    store = tmp_path / "store"
    _seed_episode(runs, ticket=80)
    monkeypatch.setattr(distill, "chat", lambda *a, **k: "[]")
    rc = distill.main(["--ticket", "80", "--runs-root", str(runs),
                       "--store", str(store)])
    assert rc == 0
    assert "0 durable facts" in capsys.readouterr().out
    assert not (store / "facts").exists()
