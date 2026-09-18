"""wq#1496: harness/fworker.py had its own independent copy of the same plain-text
`wq show` parser as orchestrator/router.py and dispatcher/self_heal.py — its docstring
even claimed 'wq has no --json', which was stale (wq#1277 added it). Embedded newlines
in a multi-line detail/notes field survived only as the literal backslash-n escape.
Drives the real wq binary against a throwaway DB, not a hand-built dict, so the actual
parsing is under test.
"""
import os
import re
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402

WQ_BIN = "/home/lumen/bin/wq"


def test_wq_show_returns_real_newlines_not_escaped_text(tmp_path, monkeypatch):
    monkeypatch.setenv("WQ_DB", str(tmp_path / "queue.db"))
    monkeypatch.delenv("WQ_AGENT", raising=False)
    detail = "line one\n\nline three after a blank"
    p = subprocess.run([WQ_BIN, "add", "--lane", "build", "--title", "wq1496 fworker multiline",
                        "--detail", detail], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    tid = int(re.search(r"added task (\d+)", p.stdout).group(1))

    t = fworker.wq_show(tid)
    assert t.get("detail") == detail, (
        f"fworker.wq_show() mangled the multi-line detail — got {t.get('detail')!r}")


def test_wq_show_missing_ticket_returns_empty_dict(tmp_path, monkeypatch):
    """fworker.wq_show()'s existing contract (unlike router's) is an empty dict, never
    a raise — many call sites do `wq_show(x).get('status')` with no try/except. The
    --json switch must not change that contract."""
    monkeypatch.setenv("WQ_DB", str(tmp_path / "queue.db"))
    monkeypatch.delenv("WQ_AGENT", raising=False)
    subprocess.run([WQ_BIN, "add", "--title", "make sure the db exists"],
                   capture_output=True, text=True)
    assert fworker.wq_show(999999) == {}


def test_ticket_holder_still_none_for_unclaimed(tmp_path, monkeypatch):
    """wq#869 contract, re-verified post wq#1496: --json returns a NULL claimed_by as
    real None, not the string 'None' — ticket_holder() must still treat that as unheld."""
    monkeypatch.setenv("WQ_DB", str(tmp_path / "queue.db"))
    monkeypatch.delenv("WQ_AGENT", raising=False)
    p = subprocess.run([WQ_BIN, "add", "--title", "unclaimed"], capture_output=True, text=True)
    tid = int(re.search(r"added task (\d+)", p.stdout).group(1))
    t = fworker.wq_show(tid)
    assert t.get("claimed_by") is None
    assert fworker.ticket_holder(t) is None
