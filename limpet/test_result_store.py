"""Unit tests for result_store.py: the wq#4508 warm-band summary decision.
Pure, no network/model client involved (same posture as test_model_router.py
and test_critic.py) -- the probe below exercises both an input INSIDE the
default band (short result) and one OUTSIDE it (a result past HOT_THRESHOLD),
so the threshold boundary can actually fail (wq#2630)."""
import os

import pytest

import result_store as rs

RESIDENT = "qwen3.5-9b"


def test_short_result_is_hot_band():
    assert rs.band_for("short") == "hot"


def test_result_past_threshold_is_warm_band():
    big = "x" * (rs.HOT_THRESHOLD + 1)
    assert rs.band_for(big) == "warm"


def test_result_at_exact_threshold_is_hot_band():
    exact = "x" * rs.HOT_THRESHOLD
    assert rs.band_for(exact) == "hot"


def test_hot_band_stores_inline_with_no_disk_write(tmp_path):
    stored = rs.store_result(str(tmp_path), "nmap", "short output")
    assert stored.band == "hot"
    assert stored.context_text == "short output"
    assert stored.cold_path is None
    assert not (tmp_path / "evidence").exists()


def test_warm_band_without_summary_raises():
    big = "x" * (rs.HOT_THRESHOLD + 1)
    with pytest.raises(rs.ResultStoreError):
        rs.store_result("/tmp/unused", "nmap", big)


def test_warm_band_writes_full_text_to_cold_disk_and_keeps_summary_in_context(tmp_path):
    big = "finding: " + "x" * (rs.HOT_THRESHOLD + 1)
    stored = rs.store_result(str(tmp_path), "nmap", big, summary="three line summary")
    assert stored.band == "warm"
    assert stored.context_text == "three line summary"
    assert stored.cold_path is not None
    assert os.path.isfile(stored.cold_path)
    with open(stored.cold_path, encoding="utf-8") as fh:
        assert fh.read() == big


def test_warm_band_cold_file_is_credential_masked(tmp_path):
    fake_key = "AKIA" + "Q" * 16  # split at the prefix seam -- never a whole credential literal
    big = fake_key + " " + "x" * (rs.HOT_THRESHOLD + 1)
    stored = rs.store_result(str(tmp_path), "nmap", big, summary="summary")
    with open(stored.cold_path, encoding="utf-8") as fh:
        content = fh.read()
    assert fake_key not in content
    assert "«MASKED:" in content


def test_summary_model_uses_fast_profile_not_reasoning_model():
    model = rs.summary_model(
        profile_models={"speed-critical": "haiku-fast", "long-context": "opus-reasoning"},
        resident_model=RESIDENT,
    )
    assert model == "haiku-fast"


def test_summary_model_falls_back_to_resident_when_fast_profile_unconfigured():
    model = rs.summary_model(profile_models={}, resident_model=RESIDENT)
    assert model == RESIDENT
