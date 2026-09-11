"""observed_burn_core.py — wq#1722. Synthetic state/unit series: reset crossing,
minimum-sample gating, fable-vs-pass-pool split, local-unit exclusion, EMA math."""
import datetime as dt
import os
import sys

DISPATCHER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DISPATCHER_DIR)
import observed_burn_core as core  # noqa: E402

NOW = dt.datetime(2026, 9, 11, 10, 0, tzinfo=dt.timezone.utc)
T0 = NOW.timestamp() - 4 * 3600  # window start: 4h before NOW


def _epoch(hours_before_now):
    return NOW.timestamp() - hours_before_now * 3600


# --- worker_hours: pool split + local exclusion -----------------------------

def test_worker_hours_counts_full_overlap_for_pass_pools():
    units = [{"model": "claude-sonnet-5", "start": _epoch(3), "end": _epoch(1)}]  # 2h
    assert core.worker_hours(units, T0, NOW.timestamp(), "session") == 2.0
    assert core.worker_hours(units, T0, NOW.timestamp(), "weekly_all") == 2.0


def test_worker_hours_clips_to_window():
    # unit runs from 6h-ago (outside the 4h window) to 1h-ago -> only 3h counts
    units = [{"model": "claude-sonnet-5", "start": _epoch(6), "end": _epoch(1)}]
    assert core.worker_hours(units, T0, NOW.timestamp(), "weekly_all") == 3.0


def test_fable_pool_only_counts_fable_units():
    units = [
        {"model": "claude-sonnet-5", "start": _epoch(3), "end": _epoch(1)},   # 2h, not fable
        {"model": "claude-fable-5", "start": _epoch(2), "end": _epoch(1)},    # 1h, fable
    ]
    assert core.worker_hours(units, T0, NOW.timestamp(), core.FABLE_POOL) == 1.0
    # pass pools count every non-local unit, fable included: 2h + 1h
    assert core.worker_hours(units, T0, NOW.timestamp(), "session") == 3.0


def test_local_units_never_count_in_any_pool():
    units = [{"model": "deepseek-v4-flash", "start": _epoch(3), "end": _epoch(1)}]
    assert core.worker_hours(units, T0, NOW.timestamp(), "session") == 0.0
    assert core.worker_hours(units, T0, NOW.timestamp(), core.FABLE_POOL) == 0.0


def test_worker_hours_empty_or_inverted_window_is_zero():
    units = [{"model": "claude-sonnet-5", "start": _epoch(3), "end": _epoch(1)}]
    assert core.worker_hours(units, NOW.timestamp(), T0, "session") == 0.0
    assert core.worker_hours([], T0, NOW.timestamp(), "session") == 0.0


# --- update_pool_sample: seed / reset crossing / min sample / EMA -----------

def test_seed_on_first_sample():
    sample, event = core.update_pool_sample(None, 10.0, "2026-09-11T14:00:00+00:00",
                                            5.0, NOW)
    assert event == "seed"
    assert sample["ema"] is None and sample["percent"] == 10.0


def test_reset_crossed_reseeds_baseline_but_keeps_ema():
    prev = {"percent": 95.0, "resets_at": "2026-09-11T09:00:00+00:00",  # already past NOW
            "sampled_at": "2026-09-11T05:00:00+00:00", "ema": 1.1}
    sample, event = core.update_pool_sample(prev, 3.0, "2026-09-18T09:00:00+00:00",
                                            10.0, NOW)
    assert event == "reset_crossed"
    assert sample["ema"] == 1.1          # unchanged, no bogus negative-delta rate baked in
    assert sample["percent"] == 3.0      # baseline reseeded to the post-reset reading


def test_insufficient_worker_hours_leaves_prev_untouched():
    prev = {"percent": 10.0, "resets_at": "2026-09-18T09:00:00+00:00",
            "sampled_at": "2026-09-11T09:00:00+00:00", "ema": None}
    sample, event = core.update_pool_sample(prev, 15.0, "2026-09-18T09:00:00+00:00",
                                            1.0, NOW)  # below MIN_WORKER_HOURS=2.0
    assert event == "insufficient_sample"
    assert sample == prev  # returned unchanged -> caller's next window grows, not resets


def test_no_burn_reseeds_without_touching_ema():
    prev = {"percent": 10.0, "resets_at": "2026-09-18T09:00:00+00:00",
            "sampled_at": "2026-09-11T09:00:00+00:00", "ema": 0.5}
    sample, event = core.update_pool_sample(prev, 9.0, "2026-09-18T09:00:00+00:00",
                                            5.0, NOW)  # percent went DOWN
    assert event == "no_burn"
    assert sample["ema"] == 0.5
    assert sample["percent"] == 9.0


def test_updated_computes_rate_and_seeds_ema_on_first_real_sample():
    prev = {"percent": 10.0, "resets_at": "2026-09-18T09:00:00+00:00",
            "sampled_at": "2026-09-11T06:00:00+00:00", "ema": None}
    sample, event = core.update_pool_sample(prev, 20.0, "2026-09-18T09:00:00+00:00",
                                            4.0, NOW)  # 10 pct over 4 worker-h = 2.5
    assert event == "updated"
    assert sample["ema"] == 2.5  # seeded: no prior ema to blend against


def test_updated_blends_ema_on_subsequent_samples():
    prev = {"percent": 20.0, "resets_at": "2026-09-18T09:00:00+00:00",
            "sampled_at": "2026-09-11T06:00:00+00:00", "ema": 2.5}
    sample, event = core.update_pool_sample(prev, 24.0, "2026-09-18T09:00:00+00:00",
                                            4.0, NOW)  # 4 pct over 4 worker-h = 1.0
    assert event == "updated"
    # EMA_ALPHA=0.3: 0.3*1.0 + 0.7*2.5 = 2.05
    assert abs(sample["ema"] - 2.05) < 1e-9


def test_ema_update_seeds_without_prior():
    assert core.ema_update(None, 1.5) == 1.5
    assert abs(core.ema_update(2.0, 1.0, alpha=0.3) - 1.7) < 1e-9
