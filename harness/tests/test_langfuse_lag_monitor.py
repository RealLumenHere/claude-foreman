"""langfuse_lag_monitor unit coverage (wq#1552).

over_threshold/run are exercised without any network access: fetch_lag_seconds and
alert are monkeypatched so the tests only assert the decision logic (skip on no
creds, skip on fetch failure, alert only when lag exceeds threshold).
"""
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import langfuse_lag_monitor as mod  # noqa: E402


def test_over_threshold_true_when_lag_exceeds():
    assert mod.over_threshold(301, 300) is True


def test_over_threshold_false_when_lag_at_or_under():
    assert mod.over_threshold(300, 300) is False
    assert mod.over_threshold(100, 300) is False


def test_over_threshold_false_when_lag_unknown():
    assert mod.over_threshold(None, 300) is False


def test_run_skips_when_no_creds(tmp_path, monkeypatch):
    missing_env = tmp_path / "nope.env"
    monkeypatch.setattr(mod, "fetch_lag_seconds", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("should not fetch without creds")))
    assert mod.run(str(missing_env), 300) == 1


def test_run_skips_when_fetch_fails(monkeypatch):
    monkeypatch.setattr(mod, "load_creds", lambda env_path: ("http://x", "pk", "sk"))
    monkeypatch.setattr(mod, "fetch_lag_seconds", lambda *a, **k: None)
    alerted = []
    monkeypatch.setattr(mod, "alert", lambda *a, **k: alerted.append(a))
    assert mod.run("unused.env", 300) == 1
    assert alerted == []


def test_run_alerts_when_over_threshold(monkeypatch):
    monkeypatch.setattr(mod, "load_creds", lambda env_path: ("http://x", "pk", "sk"))
    monkeypatch.setattr(mod, "fetch_lag_seconds", lambda *a, **k: 600)
    alerted = []
    monkeypatch.setattr(mod, "alert", lambda lag, thresh: alerted.append((lag, thresh)))
    assert mod.run("unused.env", 300) == 0
    assert alerted == [(600, 300)]


def test_run_no_alert_when_under_threshold(monkeypatch):
    monkeypatch.setattr(mod, "load_creds", lambda env_path: ("http://x", "pk", "sk"))
    monkeypatch.setattr(mod, "fetch_lag_seconds", lambda *a, **k: 10)
    alerted = []
    monkeypatch.setattr(mod, "alert", lambda *a, **k: alerted.append(a))
    assert mod.run("unused.env", 300) == 0
    assert alerted == []
