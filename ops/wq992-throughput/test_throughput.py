"""wq#992 throughput metric — unit tests against a synthetic tasks table.

Builds an in-memory-ish queue.db with done tickets at known offsets from a fixed `now`,
so the rolling-window rates, the peak-hour, the lane split, and the bar logic are all
deterministic and don't touch the live DB.
"""
import datetime
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import throughput  # noqa: E402

NOW = datetime.datetime(2026, 8, 27, 12, 0, 0)


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _db(rows):
    """rows: list of (lane, status, minutes_before_now|None). done_at=None when offset
    is None (models the 8 legacy done rows with no done_at — must be excluded)."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, lane TEXT, status TEXT, "
              "done_at TEXT)")
    for i, (lane, status, mins) in enumerate(rows, 1):
        done_at = None if mins is None else _fmt(NOW - datetime.timedelta(minutes=mins))
        c.execute("INSERT INTO tasks(id,lane,status,done_at) VALUES(?,?,?,?)",
                  (i, lane, status, done_at))
    c.commit()
    return c


def test_windows_count_only_done_in_range():
    # 2 in last hour, +3 more in last 6h, +5 more in last 24h, and one at 30h (7d only).
    rows = (
        [("build", "done", 10), ("build", "done", 50)]                 # 1h
        + [("build", "done", 120), ("research", "done", 200), ("ops", "done", 300)]  # 6h
        + [("build", "done", 600), ("build", "done", 700), ("auto", "done", 800),
           ("build", "done", 1000), ("research", "done", 1400)]        # 24h
        + [("build", "done", 1800)]                                    # 7d only (30h)
    )
    data = throughput.compute(_db(rows), NOW)
    assert data["windows"]["1h"]["done"] == 2
    assert data["windows"]["6h"]["done"] == 5
    assert data["windows"]["24h"]["done"] == 10
    assert data["windows"]["7d"]["done"] == 11
    assert data["windows"]["24h"]["per_hour"] == round(10 / 24, 2)
    assert data["headline_per_hour"] == round(10 / 24, 2)


def test_open_and_null_done_at_excluded():
    rows = [
        ("build", "done", 30),
        ("build", "open", 30),      # not done -> excluded
        ("build", "claimed", 30),   # not done -> excluded
        ("build", "done", None),    # done but no done_at -> excluded
    ]
    data = throughput.compute(_db(rows), NOW)
    assert data["windows"]["1h"]["done"] == 1
    assert data["windows"]["24h"]["done"] == 1


def test_bar_logic_below_and_above():
    below = throughput.compute(_db([("build", "done", 30)]), NOW)  # ~0.04/hr
    assert below["beating_bar"] is False
    assert below["pct_of_bar"] < 100
    # 200 done inside the last hour -> 200/24 ~= 8.33/hr over 24h, clears the 7.0 bar
    many = [("build", "done", m % 55 + 1) for m in range(200)]
    above = throughput.compute(_db(many), NOW)
    assert above["headline_per_hour"] >= throughput.BAR_PER_HOUR
    assert above["beating_bar"] is True


def test_peak_hour_and_lanes():
    # 4 in the 11:00 UTC clock-hour, 1 in 10:00 -> peak is 11:00 with 4.
    rows = [("build", "done", 5), ("build", "done", 15), ("research", "done", 25),
            ("ops", "done", 35), ("build", "done", 75)]
    data = throughput.compute(_db(rows), NOW)
    assert data["peak_hour_24h"]["done"] == 4
    assert data["peak_hour_24h"]["hour_utc"] == "2026-08-27 11"
    assert data["lanes_24h"]["build"] == 3
    assert data["lanes_24h"]["research"] == 1
    assert data["lanes_24h"]["ops"] == 1


def test_empty_db_is_zero_not_crash():
    data = throughput.compute(_db([]), NOW)
    assert data["headline_per_hour"] == 0.0
    assert data["beating_bar"] is False
    assert data["peak_hour_24h"]["done"] == 0
    assert data["lanes_24h"] == {}
