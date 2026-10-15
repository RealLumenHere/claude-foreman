"""Tests for ops/wq3809-limpet-hal/usb_boot_signatures.py (wq#3923).

No physical hardware or fixture file required beyond the shipped
usb_boot_signatures.toml itself — it is the canonical, committed data this module
reads, so the tests exercise it directly rather than a synthetic stand-in.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import usb_boot_signatures as ubs  # noqa: E402


def test_loads_at_least_100_entries():
    assert len(ubs.load_signatures()) >= 100


def test_every_entry_has_required_fields_and_valid_confidence():
    for sig in ubs.load_signatures():
        assert sig.match_type in ("vid_pid", "vid_pid_wildcard", "usb_class")
        assert sig.vendor and sig.soc_family and sig.mode_name and sig.recommended_tool
        assert 0.0 <= sig.confidence <= 1.0


def test_lookup_finds_qualcomm_edl():
    hits = ubs.lookup("05c6", "9008")
    assert any(h.mode_name.startswith("EDL") for h in hits)
    top = hits[0]
    assert top.recommended_tool == "edl / qdl"
    assert top.confidence >= 0.9


def test_lookup_finds_allwinner_fel_across_soc_series():
    hits = ubs.lookup("1f3a", "efe8")
    assert len(hits) == 1
    assert "H6" in hits[0].soc_family and "A10" in hits[0].soc_family


def test_lookup_matches_oem_wildcard_on_vid_only():
    hits = ubs.lookup("22b8", "1234")  # arbitrary PID Motorola never specifically listed
    assert any(h.match_type == "vid_pid_wildcard" for h in hits)


def test_lookup_matches_usb_class_independent_of_vid():
    hits = ubs.lookup("ffff", "ffff", class_code="fe:01:00")
    assert any(h.mode_name.startswith("DFU class") for h in hits)


def test_lookup_returns_nothing_for_unlisted_device():
    # Probe drawn from outside the matched population (wq#2630): a VID:PID this
    # table does not and should not recognize. If this ever starts matching,
    # something in lookup() has gone overly permissive.
    assert ubs.lookup("dead", "beef") == []


def test_lookup_sorts_highest_confidence_first():
    hits = ubs.lookup("05c6", "9008")
    confidences = [h.confidence for h in hits]
    assert confidences == sorted(confidences, reverse=True)
