"""pat-scan --alert severity (wq#2911): CRITICAL means a LIVE token; a world-readable file
holding only dead literals is WARN (queued to aq, never paged, exit 0).

Runs against staged/wq523_pat_residue.py; PAT_SCAN=/path/to/other/copy points it at a
different copy (the pre-wq2911 live file fails the WARN cases — that is the regression).
Filesystem walk, canonical homes, and aq/ntfy are all stubbed: no host scan, no alert.
"""
import hashlib
import importlib.util
import os
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PAT_SCAN = os.environ.get("PAT_SCAN", os.path.join(HERE, "staged", "wq523_pat_residue.py"))


def _tok(ch):
    return ("gh" "p_" + ch * 36).encode()


def _fp(t):
    return hashlib.sha256(t).hexdigest()[:12]


@pytest.fixture
def pat(monkeypatch):
    spec = importlib.util.spec_from_file_location("pat_under_test", PAT_SCAN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = []
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda argv, **kw: calls.append(argv) or
                        subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(mod, "canonical_set", lambda: set())
    monkeypatch.setattr(mod, "is_false_positive", lambda p, t=None: False)
    monkeypatch.setattr(mod, "live_fingerprints", lambda: {_fp(_tok("L"))})
    mod._calls = calls
    return mod


def _files(tmp_path, monkeypatch, pat, spec):
    index = {}
    for name, (mode, tok) in spec.items():
        p = tmp_path / name
        p.write_text("x")
        os.chmod(p, mode)
        index[str(p)] = {tok}
    monkeypatch.setattr(pat, "build_index", lambda *a, **k: (index, []))


def _lines(out, tag):
    return [l for l in out.splitlines() if l.strip().startswith(tag + " ")]


def _paged(calls):
    return [c for c in calls if c and c[0].endswith("ntfy-send")]


def test_world_readable_dead_literal_is_warn_not_critical(tmp_path, monkeypatch, capsys, pat):
    _files(tmp_path, monkeypatch, pat, {"replay-AC1.log": (0o644, _tok("D"))})
    rc = pat.cmd_alert()
    out = capsys.readouterr().out
    assert rc == 0
    assert not _lines(out, "CRITICAL")
    assert len(_lines(out, "WARN")) == 1 and "no live match" in out
    assert not _paged(pat._calls)
    assert any(c[:3] == ["/home/lumen/bin/aq", "add", "pat-scan-warn"] for c in pat._calls)


def test_live_token_is_critical_and_names_readability(tmp_path, monkeypatch, capsys, pat):
    _files(tmp_path, monkeypatch, pat, {
        "live-open": (0o644, _tok("L")),
        "live-closed": (0o600, _tok("L")),
        "dead-closed": (0o600, _tok("D")),
    })
    rc = pat.cmd_alert()
    out = capsys.readouterr().out
    crit = _lines(out, "CRITICAL")
    assert rc == 1
    assert len(crit) == 2 and all("LIVE token" in l for l in crit)
    assert any("live-open" in l and "WORLD-READABLE" in l for l in crit)
    assert not _lines(out, "WARN")
    assert "1 noted" in out
    assert len(_paged(pat._calls)) == 1


def test_no_token_value_is_printed(tmp_path, monkeypatch, capsys, pat):
    _files(tmp_path, monkeypatch, pat, {"a": (0o644, _tok("D")), "b": (0o644, _tok("L"))})
    pat.cmd_alert()
    out = capsys.readouterr().out
    assert _tok("D").decode() not in out and _tok("L").decode() not in out
