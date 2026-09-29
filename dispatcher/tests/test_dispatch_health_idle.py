"""wq#3106: a long budget stop pages through aq instead of waiting for a human to notice.

Fixture: real dispatcher.log passes cut from 2026-09-26..28 (capability lint/skip lines
dropped). The rotated file holds the last spawning pass (09-26 06:07Z) and a 12:02Z pass
at `halt no_slots` "0 cloud unit(s) at ceiling 0". The live file holds three ceiling-0
passes (09-27 00:25Z, 13:58Z, 09-28 03:04Z) and the 09-28 03:10Z pass that spawned 8.
Every ceiling-0 pass is `healthy` to classify_passes; the idle rule is what has to fire.
"""
import gzip
import os
import shutil
import sqlite3
import sys
from datetime import datetime

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import dispatch_health as dh  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
LIVE = os.path.join(FIX, "wq3106_idle_window.jsonl")
ROTATED = os.path.join(FIX, "wq3106_idle_window_rotated.jsonl")
AQ = "/home/lumen/bin/aq"
H = 3600


def _at(iso):
    return datetime.fromisoformat(iso)


def _live_until(tmp_path, iso):
    """The live log as it stood at `iso`: fixture lines stamped at or before it."""
    lines = [l for l in open(LIVE) if l[8:33] <= iso]
    p = tmp_path / "dispatcher.log"
    p.write_text("".join(lines))
    return str(p)


def _with_rotation(log):
    with open(ROTATED, "rb") as src, gzip.open(log + ".1.gz", "wb") as dst:
        shutil.copyfileobj(src, dst)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    db = tmp_path / "alerts.db"
    monkeypatch.setenv("AQ_DB", str(db))
    monkeypatch.setattr(dh, "AQ_DB", str(db))
    monkeypatch.setattr(dh, "AQ_BIN", AQ)
    monkeypatch.setattr(dh, "STATE_PATH", str(tmp_path / "health_state.json"))
    monkeypatch.setattr(dh, "_LEGACY_STATE_PATH", str(tmp_path / "legacy.json"))
    sent = []
    monkeypatch.setattr(dh, "_ntfy", lambda msg, **k: sent.append((k.get("priority"), msg)))
    return tmp_path, db, sent


def _rows(db):
    if not db.exists():
        return []
    c = sqlite3.connect(db)
    rows = c.execute("SELECT source, key, title, message, status, count FROM alerts").fetchall()
    c.close()
    return rows


def test_ceiling_zero_passes_are_healthy_to_the_stall_rule():
    """The gap itself: the pre-existing rule cannot see this stop."""
    events = dh.parse_log_tail(LIVE, 10_000)
    verdicts = {pid: v for pid, _, v, _ in dh.classify_passes(events)}
    assert verdicts["fdisp-20260927T135846Z-3119891"] == "healthy"
    assert verdicts["fdisp-20260928T030450Z-2479550"] == "healthy"


def test_idle_fires_on_the_real_window_and_names_the_ceiling(tmp_path):
    log = _live_until(tmp_path, "2026-09-27T14:00:00")
    _with_rotation(log)
    f = dh.scan_idle(log, now=_at("2026-09-27T14:00:00+00:00"), window_s=6 * H)
    assert f is not None
    assert f["dispatchable"] == 42
    assert "fdisp-20260927T135846Z-3119891" in f["summary"]
    assert "ceiling 0" in f["cause"]
    assert "weekly_all: spendable 0.0%" in f["cause"]


def test_spawn_inside_window_is_not_idle(tmp_path):
    """The 03:10Z pass spawned 8: an hour later the fleet is not idle."""
    log = _live_until(tmp_path, "2026-09-28T04:10:00")
    assert dh.scan_idle(log, now=_at("2026-09-28T04:10:00+00:00"), window_s=6 * H) is None


def test_lines_after_now_are_ignored():
    """Whole live file, now = one minute before the 03:10Z recovery pass: still idle.
    A scan that let the later spawn count would call this minute healthy."""
    f = dh.scan_idle(LIVE, now=_at("2026-09-28T03:09:00+00:00"), window_s=6 * H)
    assert f is not None and f["dispatchable"] == 43


def test_rotation_gap_reads_the_gz_and_finds_the_last_spawn(tmp_path):
    """09-26 11:00Z: the only spawn in the window (06:07Z) is in the rotated file.
    Without reading .1.gz this is 'not covered' (None); with it, still None but because
    of the spawn — so move `now` to 12:10Z (6h after the spawn) and it must fire."""
    log = str(tmp_path / "dispatcher.log")
    open(log, "w").close()
    _with_rotation(log)
    assert dh.scan_idle(log, now=_at("2026-09-26T11:00:00+00:00"), window_s=6 * H) is None
    f = dh.scan_idle(log, now=_at("2026-09-26T12:10:00+00:00"), window_s=6 * H)
    assert f is not None and "ceiling 0" in f["cause"]


def test_uncovered_window_does_not_guess(tmp_path):
    """Live log only, no rotation: at 09-27 03:00Z the log starts 00:25Z, so 6h back is
    unknown — no alarm rather than a false one."""
    log = _live_until(tmp_path, "2026-09-27T03:00:00")
    assert dh.scan_idle(log, now=_at("2026-09-27T03:00:00+00:00"), window_s=6 * H) is None


def test_nothing_dispatchable_is_not_idle(tmp_path):
    log = _live_until(tmp_path, "2026-09-27T14:00:00")
    _with_rotation(log)
    text = open(log).read().replace('"dispatchable": 42', '"dispatchable": 0')
    open(log, "w").write(text)
    assert dh.scan_idle(log, now=_at("2026-09-27T14:00:00+00:00"), window_s=6 * H) is None


def test_check_idle_opens_one_aq_row_then_resolves(sandbox, monkeypatch):
    tmp_path, db, sent = sandbox
    idle_log = _live_until(tmp_path, "2026-09-27T14:00:00")
    _with_rotation(idle_log)
    now = _at("2026-09-27T14:00:00+00:00")

    word, msg = dh.check_idle(idle_log, now=now, window_s=6 * H)
    assert word == "idle"
    rows = _rows(db)
    assert len(rows) == 1
    src, key, title, message, status, count = rows[0]
    assert (src, key, status, count) == ("dispatch-health", "idle-window", "open", 1)
    assert "ceiling 0" in message
    assert len(sent) == 1 and sent[0][0] == "high"

    # Next tick, still idle: no second ntfy, no second row.
    word, _ = dh.check_idle(idle_log, now=now, window_s=6 * H)
    assert word == "idle" and len(sent) == 1 and len(_rows(db)) == 1

    # Spawning resumes (the real 03:10Z pass): row resolved, state cleared.
    busy_log = _live_until(tmp_path, "2026-09-28T04:10:00")
    word, _ = dh.check_idle(busy_log, now=_at("2026-09-28T04:10:00+00:00"), window_s=6 * H)
    assert word == "recovered"
    assert _rows(db)[0][4] == "resolved"
    assert "idle_alert_ts" not in dh.load_state()


def test_dry_run_writes_nothing(sandbox):
    tmp_path, db, sent = sandbox
    log = _live_until(tmp_path, "2026-09-27T14:00:00")
    _with_rotation(log)
    word, _ = dh.check_idle(log, dry_run=True, now=_at("2026-09-27T14:00:00+00:00"),
                            window_s=6 * H)
    assert word == "idle" and _rows(db) == [] and sent == []
