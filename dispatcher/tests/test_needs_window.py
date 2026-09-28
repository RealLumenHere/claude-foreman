"""wq#2915 (wq#1401 items 1/5): the [needs-window] gate.

Same zero-quota harness as test_dispatcher.py: the real `wq` against a scratch WQ_DB,
fake lumen-run recording every unit start. lumen_window.py itself lives outside this repo
(/home/lumen/bin), so it is never imported for real here — fd.LUMEN_WINDOW_PATH is pointed
at small fake modules that stand in for it, the same seam foreman_dispatcher.py uses via
_lumen_window_module() (dispatch_health._cred_module's load-by-path pattern).
"""
import json
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
from test_dispatcher import (  # noqa: E402,F401
    add_ticket, denv, fd, log_events, lumen_calls, write_config)

REPO = os.path.dirname(os.path.dirname(TESTS_DIR))

FAKE_OPEN = '''
import datetime as dt
OK = 0
def load_config():
    return {}
def load_state():
    return {}
def now_utc():
    return dt.datetime.now(dt.timezone.utc)
def gate(cfg, state, name, now, est_sec=0, node=None, tags=(), release=None):
    return OK, "open, 3600s left"
'''

FAKE_CLOSED = '''
import datetime as dt
OK = 0
CLOSED = 10
def load_config():
    return {}
def load_state():
    return {}
def now_utc():
    return dt.datetime.now(dt.timezone.utc)
def gate(cfg, state, name, now, est_sec=0, node=None, tags=(), release=None):
    return CLOSED, "fleet closed; next opens Mon 2026-09-28 02:00 PT"
'''

FAKE_RAISES_ON_READ = '''
OK = 0
def load_config():
    raise RuntimeError("config unreadable")
def load_state():
    return {}
def now_utc():
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc)
def gate(cfg, state, name, now, est_sec=0, node=None, tags=(), release=None):
    return OK, "unreachable"
'''


def _fake_window(denv, monkeypatch, body):
    path = denv["tmp"] / "fake_lumen_window.py"
    path.write_text(body)
    monkeypatch.setattr(fd, "LUMEN_WINDOW_PATH", str(path))


def test_shipped_config_activates_needs_window():
    """Activated by the reviewed config commit (wq#2935 CONFIRMed wq#2915): the committed
    block exists and is ON. Flip back to False here AND in config.json to go dark again."""
    with open(os.path.join(REPO, "dispatcher", "config.json")) as f:
        cfg = json.load(f)
    assert cfg["needs_window"]["enabled"] is True


def test_dark_switch_ignores_marker_even_when_window_closed(denv, monkeypatch):
    """needs_window.enabled absent/false: the marker is inert, exactly as before wq#2915."""
    _fake_window(denv, monkeypatch, FAKE_CLOSED)
    tid = add_ticket("[needs-window] fleet upgrade")
    assert fd.run_pass() == 0
    assert log_events(denv, "skip", ticket=tid) == []
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_marker_skipped_while_window_closed(denv, monkeypatch):
    write_config(denv, needs_window={"enabled": True})
    _fake_window(denv, monkeypatch, FAKE_CLOSED)
    tid = add_ticket("[needs-window] fleet upgrade")
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1
    assert skips[0]["stage"] == "needs_window" and skips[0]["reason"] == "needs_window_closed"
    assert "fleet closed" in skips[0]["detail"]
    assert lumen_calls(denv) == []


def test_marker_dispatches_while_window_open(denv, monkeypatch):
    write_config(denv, needs_window={"enabled": True})
    _fake_window(denv, monkeypatch, FAKE_OPEN)
    tid = add_ticket("[needs-window] fleet upgrade")
    assert fd.run_pass() == 0
    assert log_events(denv, "skip", ticket=tid) == []
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_unmarked_ticket_ignores_closed_window(denv, monkeypatch):
    """Only [needs-window]-titled tickets answer to this gate at all."""
    write_config(denv, needs_window={"enabled": True})
    _fake_window(denv, monkeypatch, FAKE_CLOSED)
    tid = add_ticket("ordinary ticket, never mind the window")
    assert fd.run_pass() == 0
    assert log_events(denv, "skip", ticket=tid) == []
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_fail_closed_when_lumen_window_module_missing(denv, monkeypatch):
    write_config(denv, needs_window={"enabled": True})
    monkeypatch.setattr(fd, "LUMEN_WINDOW_PATH", str(denv["tmp"] / "no-such-lumen-window.py"))
    tid = add_ticket("[needs-window] fleet upgrade")
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1
    assert skips[0]["stage"] == "needs_window" and skips[0]["reason"] == "needs_window_closed"
    assert "fail closed" in skips[0]["detail"]
    assert lumen_calls(denv) == []


def test_fail_closed_when_lumen_window_config_unreadable(denv, monkeypatch):
    write_config(denv, needs_window={"enabled": True})
    _fake_window(denv, monkeypatch, FAKE_RAISES_ON_READ)
    tid = add_ticket("[needs-window] fleet upgrade")
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1
    assert skips[0]["stage"] == "needs_window" and skips[0]["reason"] == "needs_window_closed"
    assert "fail closed" in skips[0]["detail"]
    assert lumen_calls(denv) == []
