"""langfuse_lag_monitor unit coverage (wq#1552, canary redesign wq#3124).

run() is exercised without any network access: fetch_trace_status, ingest_canary and
alert are monkeypatched so the tests only assert the decision logic — skip on no
creds, first-pass canary planting, confirm-then-replant, staleness alerting, and the
positive-control property that dead ingestion cannot stay green.
"""
import json
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import langfuse_lag_monitor as mod  # noqa: E402

CREDS = ("http://x", "pk", "sk")


def _wire(monkeypatch, trace_status=200, ingested_id="new-canary", alerted=None):
    monkeypatch.setattr(mod, "load_creds", lambda env_path: CREDS)
    monkeypatch.setattr(mod, "fetch_trace_status", lambda *a, **k: trace_status)
    monkeypatch.setattr(mod, "ingest_canary", lambda *a, **k: ingested_id)
    monkeypatch.setattr(mod, "alert",
                        lambda lag, thresh: (alerted if alerted is not None else []).append((lag, thresh)))


def test_over_threshold_true_when_lag_exceeds():
    assert mod.over_threshold(2701, 2700) is True


def test_over_threshold_false_when_lag_at_or_under():
    assert mod.over_threshold(2700, 2700) is False
    assert mod.over_threshold(100, 2700) is False


def test_over_threshold_false_when_lag_unknown():
    assert mod.over_threshold(None, 2700) is False


def test_run_skips_when_no_creds(tmp_path, monkeypatch):
    missing_env = tmp_path / "nope.env"
    monkeypatch.setattr(mod, "ingest_canary", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("should not ingest without creds")))
    assert mod.run(str(missing_env), 2700, state_path=str(tmp_path / "state.json")) == 1


def test_first_run_plants_canary_no_alert(tmp_path, monkeypatch):
    alerted = []
    _wire(monkeypatch, alerted=alerted)
    state_path = str(tmp_path / "state.json")
    assert mod.run("unused.env", 2700, state_path=state_path, now=1000.0) == 0
    state = json.load(open(state_path))
    assert state["pending_id"] == "new-canary"
    assert state["pending_ingested_at"] == 1000.0
    assert alerted == []


def test_confirmed_canary_advances_last_ok_and_replants(tmp_path, monkeypatch):
    alerted = []
    _wire(monkeypatch, trace_status=200, alerted=alerted)
    state_path = str(tmp_path / "state.json")
    json.dump({"pending_id": "old", "pending_ingested_at": 100.0}, open(state_path, "w"))
    assert mod.run("unused.env", 2700, state_path=state_path, now=1000.0) == 0
    state = json.load(open(state_path))
    assert state["last_ok_at"] == 1000.0
    assert state["pending_id"] == "new-canary"
    assert alerted == []


def test_missing_canary_over_threshold_alerts(tmp_path, monkeypatch):
    alerted = []
    _wire(monkeypatch, trace_status=404, alerted=alerted)
    state_path = str(tmp_path / "state.json")
    json.dump({"pending_id": "old", "pending_ingested_at": 100.0, "last_ok_at": 100.0},
              open(state_path, "w"))
    assert mod.run("unused.env", 2700, state_path=state_path, now=100.0 + 2701) == 0
    assert alerted == [(2701.0, 2700)]
    # unverified pending is preserved, not papered over with a fresh canary
    assert json.load(open(state_path))["pending_id"] == "old"


def test_missing_canary_under_threshold_stays_quiet(tmp_path, monkeypatch):
    alerted = []
    _wire(monkeypatch, trace_status=404, alerted=alerted)
    state_path = str(tmp_path / "state.json")
    json.dump({"pending_id": "old", "pending_ingested_at": 900.0, "last_ok_at": 900.0},
              open(state_path, "w"))
    assert mod.run("unused.env", 2700, state_path=state_path, now=1000.0) == 0
    assert alerted == []


def test_dead_ingestion_cannot_stay_green(tmp_path, monkeypatch):
    """Positive control: POST failing every pass must eventually alert, because
    last_ok_at stops advancing — the wq#3122 failure mode."""
    alerted = []
    monkeypatch.setattr(mod, "load_creds", lambda env_path: CREDS)
    monkeypatch.setattr(mod, "ingest_canary", lambda *a, **k: None)
    monkeypatch.setattr(mod, "fetch_trace_status", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no pending to fetch")))
    monkeypatch.setattr(mod, "alert", lambda lag, thresh: alerted.append((lag, thresh)))
    state_path = str(tmp_path / "state.json")
    json.dump({"last_ok_at": 100.0}, open(state_path, "w"))
    mod.run("unused.env", 2700, state_path=state_path, now=100.0 + 3000)
    assert alerted == [(3000.0, 2700)]


def test_total_transport_failure_is_a_skip(tmp_path, monkeypatch):
    alerted = []
    monkeypatch.setattr(mod, "load_creds", lambda env_path: CREDS)
    monkeypatch.setattr(mod, "fetch_trace_status", lambda *a, **k: None)
    monkeypatch.setattr(mod, "ingest_canary", lambda *a, **k: None)
    monkeypatch.setattr(mod, "alert", lambda lag, thresh: alerted.append((lag, thresh)))
    state_path = str(tmp_path / "state.json")
    json.dump({"pending_id": "old", "pending_ingested_at": 900.0, "last_ok_at": 900.0},
              open(state_path, "w"))
    assert mod.run("unused.env", 2700, state_path=state_path, now=1000.0) == 1
    assert alerted == []


def test_corrupt_state_restarts_measurement(tmp_path, monkeypatch):
    alerted = []
    _wire(monkeypatch, alerted=alerted)
    state_path = str(tmp_path / "state.json")
    open(state_path, "w").write("not json{")
    assert mod.run("unused.env", 2700, state_path=state_path, now=1000.0) == 0
    assert json.load(open(state_path))["pending_id"] == "new-canary"
    assert alerted == []
