"""observed_burn_writer.py — wq#1722. Synthetic governor state.json + unit series
through a full poll_once(), incl. a reset crossing and the fable/pass-pool split.
Monkeypatches the two subprocess-facing functions (running_units/exited_units) so
this never touches a real journalctl or lumen-run."""
import datetime as dt
import json
import os
import sys

DISPATCHER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DISPATCHER_DIR)
import observed_burn_writer as w  # noqa: E402

NOW = dt.datetime(2026, 9, 11, 12, 0, tzinfo=dt.timezone.utc)


def _paths(tmp_path, monkeypatch):
    gov = tmp_path / "state.json"
    sampler = tmp_path / "sampler_state.json"
    out = tmp_path / "observed_burn.json"
    monkeypatch.setattr(w, "GOVERNOR_STATE_PATH", str(gov))
    monkeypatch.setattr(w, "SAMPLER_STATE_PATH", str(sampler))
    monkeypatch.setattr(w, "OUTPUT_PATH", str(out))
    return gov, sampler, out


def test_seed_poll_writes_no_ema_yet(tmp_path, monkeypatch):
    gov, sampler, out = _paths(tmp_path, monkeypatch)
    gov.write_text(json.dumps({"limits": {
        "session": {"percent": 10.0, "resets_at": "2026-09-11T17:00:00+00:00"},
        "weekly_all": {"percent": 50.0, "resets_at": "2026-09-18T07:00:00+00:00"},
    }}))
    monkeypatch.setattr(w, "running_units", lambda: [])
    monkeypatch.setattr(w, "exited_units", lambda since_iso: [])

    observed = w.poll_once(now=NOW)
    assert "session" not in observed and "weekly_all" not in observed  # no ema yet
    assert observed["_events"]["session"] == "seed"
    sampler_state = json.loads(sampler.read_text())
    assert sampler_state["session"]["percent"] == 10.0


def test_second_poll_computes_rate_from_worker_hours_and_fable_split(tmp_path, monkeypatch):
    gov, sampler, out = _paths(tmp_path, monkeypatch)
    t0 = NOW - dt.timedelta(hours=4)
    sampler.write_text(json.dumps({
        "session": {"percent": 10.0, "resets_at": "2026-09-11T17:00:00+00:00",
                    "sampled_at": t0.isoformat(), "ema": None},
        "fable_weekly_scoped": {"percent": 5.0, "resets_at": "2026-09-18T07:00:00+00:00",
                                 "sampled_at": t0.isoformat(), "ema": None},
    }))
    gov.write_text(json.dumps({"limits": {
        "session": {"percent": 18.0, "resets_at": "2026-09-11T17:00:00+00:00"},
        "fable_weekly_scoped": {"percent": 9.0, "resets_at": "2026-09-18T07:00:00+00:00"},
    }}))
    # one sonnet unit running the whole 4h window (draws session), one fable unit
    # running the whole window too (draws session AND fable_weekly_scoped)
    monkeypatch.setattr(w, "running_units", lambda: [
        ("fworker-wq10", int(t0.timestamp())),
        ("fworker-wq11", int(t0.timestamp())),
    ])
    monkeypatch.setattr(w, "exited_units", lambda since_iso: [])
    monkeypatch.setattr(w.concurrency, "ticket_model",
                        lambda tid, wq_bin=None: {"10": "claude-sonnet-5",
                                                   "11": "claude-fable-5"}[tid])

    observed = w.poll_once(now=NOW)
    # session: 8 pct over 8 worker-h (2 units x 4h) = 1.0 %/worker-h
    assert observed["session"] == 1.0
    # fable_weekly_scoped: 4 pct over 4 worker-h (only the fable unit) = 1.0 %/worker-h
    assert observed["fable_weekly_scoped"] == 1.0
    assert observed["_events"]["session"] == "updated"
    assert "_note" in observed and "Frank" in observed["_note"]


def test_reset_crossing_is_skipped_end_to_end(tmp_path, monkeypatch):
    gov, sampler, out = _paths(tmp_path, monkeypatch)
    t0 = NOW - dt.timedelta(hours=2)
    sampler.write_text(json.dumps({
        "weekly_all": {"percent": 95.0, "resets_at": "2026-09-11T11:00:00+00:00",  # < NOW
                       "sampled_at": t0.isoformat(), "ema": 0.4},
    }))
    gov.write_text(json.dumps({"limits": {
        "weekly_all": {"percent": 3.0, "resets_at": "2026-09-18T11:00:00+00:00"},
    }}))
    monkeypatch.setattr(w, "running_units", lambda: [("fworker-wq20", int(t0.timestamp()))])
    monkeypatch.setattr(w, "exited_units", lambda since_iso: [])
    monkeypatch.setattr(w.concurrency, "ticket_model",
                        lambda tid, wq_bin=None: "claude-sonnet-5")

    observed = w.poll_once(now=NOW)
    assert observed["_events"]["weekly_all"] == "reset_crossed"
    assert observed["weekly_all"] == 0.4  # EMA carried through the reset, not recomputed
