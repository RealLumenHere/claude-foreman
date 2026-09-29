"""langfuse_trace_rate_monitor unit coverage (wq#3122).

under_threshold/run are exercised without any network access: fetch_trace_count and
alert are monkeypatched so the tests only assert the decision logic (skip on no
creds, skip on fetch failure, alert only when the count is at/under the floor).
"""
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import langfuse_trace_rate_monitor as mod  # noqa: E402


def test_under_floor_true_when_count_at_floor():
    assert mod.under_floor(66, 66) is True


def test_under_floor_true_when_count_below_floor():
    assert mod.under_floor(11, 66) is True


def test_under_floor_false_when_count_above_floor():
    assert mod.under_floor(630, 66) is False


def test_under_floor_false_when_count_unknown():
    assert mod.under_floor(None, 66) is False


def test_run_skips_when_no_creds(tmp_path):
    missing_env = tmp_path / "nope.env"
    assert mod.run(str(missing_env), 3600, 66) == 1


def test_run_skips_when_fetch_fails(monkeypatch):
    monkeypatch.setattr(mod, "load_creds", lambda env_path: ("http://x", "pk", "sk"))
    monkeypatch.setattr(mod, "fetch_trace_count", lambda *a, **k: None)
    alerted = []
    monkeypatch.setattr(mod, "alert", lambda *a, **k: alerted.append(a))
    assert mod.run("unused.env", 3600, 66) == 1
    assert alerted == []


def test_run_alerts_when_at_or_under_floor(monkeypatch):
    monkeypatch.setattr(mod, "load_creds", lambda env_path: ("http://x", "pk", "sk"))
    monkeypatch.setattr(mod, "fetch_trace_count", lambda *a, **k: 20)
    alerted = []
    monkeypatch.setattr(mod, "alert", lambda count, floor, window: alerted.append((count, floor, window)))
    assert mod.run("unused.env", 3600, 66) == 0
    assert alerted == [(20, 66, 3600)]


def test_run_no_alert_when_over_floor(monkeypatch):
    monkeypatch.setattr(mod, "load_creds", lambda env_path: ("http://x", "pk", "sk"))
    monkeypatch.setattr(mod, "fetch_trace_count", lambda *a, **k: 400)
    alerted = []
    monkeypatch.setattr(mod, "alert", lambda *a, **k: alerted.append(a))
    assert mod.run("unused.env", 3600, 66) == 0
    assert alerted == []


def test_fetch_trace_count_reads_meta_total_items(monkeypatch):
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"data": [], "meta": {"page": 1, "limit": 1, "totalItems": 42, "totalPages": 1}}'

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=10: FakeResp())
    assert mod.fetch_trace_count("http://x", "pk", "sk", 3600) == 42


def test_fetch_trace_count_returns_none_on_transport_error(monkeypatch):
    def raise_err(req, timeout=10):
        raise OSError("connection refused")

    monkeypatch.setattr(mod.urllib.request, "urlopen", raise_err)
    assert mod.fetch_trace_count("http://x", "pk", "sk", 3600) is None
