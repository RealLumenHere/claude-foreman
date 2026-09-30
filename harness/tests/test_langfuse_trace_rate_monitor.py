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


# ---- wq#3211: the monitor must watch the project LiteLLM writes to, by trace name ----------

def test_defaults_target_the_litellm_project_not_the_foreman_project():
    """Regression: it used to read langfuse-foreman.env and count all foreman traces, so a
    46 h loss of every LiteLLM trace was invisible. The default env file must be the one the
    litellm container loads, and the default filter LiteLLM's own trace name."""
    assert mod.DEFAULT_ENV_PATH == "/home/lumen/agent/.env"
    assert "foreman" not in mod.DEFAULT_ENV_PATH
    assert mod.DEFAULT_TRACE_NAME == "litellm-acompletion"
    assert mod.DEFAULT_FLOOR == 0
    assert mod.DEFAULT_HOST.startswith("http://127.0.0.1")  # agent/.env's host is docker-only


def test_fetch_trace_count_sends_the_name_filter(monkeypatch):
    seen = {}

    class R:
        def read(self):
            return b'{"meta": {"totalItems": 7}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return R()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    assert mod.fetch_trace_count("http://h", "pk", "sk", 60, name="litellm-acompletion") == 7
    assert "name=litellm-acompletion" in seen["url"]
    mod.fetch_trace_count("http://h", "pk", "sk", 60)
    assert "name=" not in seen["url"]


def test_run_uses_host_override_and_name_and_names_the_project_in_the_alert(tmp_path, monkeypatch):
    env = tmp_path / "e.env"
    env.write_text("LANGFUSE_HOST=http://langfuse-web:3000\nLANGFUSE_PUBLIC_KEY=pk-lf-TESTPROJECT-1\nLANGFUSE_SECRET_KEY=sk-lf-x\n")
    got, msgs = {}, []
    monkeypatch.setattr(mod, "fetch_trace_count",
                        lambda host, pk, sk, window, name=None: got.update(host=host, name=name) or 0)
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: msgs.append(cmd[-1]))
    assert mod.run(str(env), 2700, 0, name="litellm-acompletion", host_override="http://127.0.0.1:3020") == 0
    assert got == {"host": "http://127.0.0.1:3020", "name": "litellm-acompletion"}
    assert msgs and "litellm-acompletion" in msgs[0] and "pk-lf-TESTPR" in msgs[0]


def test_zero_litellm_traces_alerts_but_a_healthy_count_does_not(tmp_path, monkeypatch):
    env = tmp_path / "e.env"
    env.write_text("LANGFUSE_HOST=http://h\nLANGFUSE_PUBLIC_KEY=pk-lf-a\nLANGFUSE_SECRET_KEY=sk-lf-b\n")
    msgs = []
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: msgs.append(cmd))
    for count, expect_alert in ((0, True), (13, False)):
        msgs.clear()
        monkeypatch.setattr(mod, "fetch_trace_count", lambda *a, _c=count, **k: _c)
        mod.run(str(env), 2700, 0, name="litellm-acompletion")
        assert bool(msgs) is expect_alert


# ---- wq#3221: a breach that persists past ESCALATE_AFTER_S opens one fleet-fires ticket ----

def test_escalate_records_first_breach_and_does_not_ticket_immediately(tmp_path):
    state_path = str(tmp_path / "state.json")
    calls = []
    state = mod.escalate_if_persistent(True, 1000.0, state_path=state_path,
                                       escalate_after_s=7200, wq_add=lambda t, d: calls.append((t, d)) or True)
    assert state["first_breach_ts"] == 1000.0
    assert state["escalated"] is False
    assert calls == []


def test_escalate_tickets_once_breach_exceeds_threshold(tmp_path):
    state_path = str(tmp_path / "state.json")
    mod.escalate_if_persistent(True, 1000.0, state_path=state_path, escalate_after_s=7200,
                               wq_add=lambda t, d: True)
    calls = []
    state = mod.escalate_if_persistent(True, 1000.0 + 7201, state_path=state_path, escalate_after_s=7200,
                                       wq_add=lambda t, d: calls.append((t, d)) or True,
                                       trace_count=0, floor_count=0, window_seconds=2700, name="litellm-acompletion")
    assert len(calls) == 1
    assert "trace-rate floor breach" in calls[0][0]
    assert state["escalated"] is True

    # a further breach on the same still-open condition must NOT ticket again
    calls.clear()
    mod.escalate_if_persistent(True, 1000.0 + 20000, state_path=state_path, escalate_after_s=7200,
                               wq_add=lambda t, d: calls.append((t, d)) or True)
    assert calls == []


def test_escalate_clears_state_on_recovery_so_the_next_breach_can_reticket(tmp_path):
    state_path = str(tmp_path / "state.json")
    mod.escalate_if_persistent(True, 1000.0, state_path=state_path, escalate_after_s=7200,
                               wq_add=lambda t, d: True)
    mod.escalate_if_persistent(True, 1000.0 + 7201, state_path=state_path, escalate_after_s=7200,
                               wq_add=lambda t, d: True)
    cleared = mod.escalate_if_persistent(False, 1000.0 + 30000, state_path=state_path, escalate_after_s=7200,
                                         wq_add=lambda t, d: True)
    assert cleared == {}

    calls = []
    state = mod.escalate_if_persistent(True, 1000.0 + 30001, state_path=state_path, escalate_after_s=7200,
                                       wq_add=lambda t, d: calls.append((t, d)) or True)
    assert state["first_breach_ts"] == 1000.0 + 30001
    assert calls == []  # fresh breach, threshold not reached yet


def test_run_calls_escalate_if_persistent(tmp_path, monkeypatch):
    env = tmp_path / "e.env"
    env.write_text("LANGFUSE_HOST=http://h\nLANGFUSE_PUBLIC_KEY=pk-lf-a\nLANGFUSE_SECRET_KEY=sk-lf-b\n")
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: None)
    monkeypatch.setattr(mod, "fetch_trace_count", lambda *a, **k: 0)
    seen = {}
    monkeypatch.setattr(mod, "escalate_if_persistent",
                        lambda breached, now_ts, **kw: seen.update(breached=breached, kw=kw))
    mod.run(str(env), 2700, 0, name="litellm-acompletion")
    assert seen["breached"] is True
    assert seen["kw"]["trace_count"] == 0 and seen["kw"]["floor_count"] == 0
