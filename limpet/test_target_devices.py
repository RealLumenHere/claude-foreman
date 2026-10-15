"""Tests for target_devices.py (wq#3940): the per-vendor-family catalog under
target-devices.d/*.toml and the udev rules it derives.

No physical hardware or fixture file required beyond the shipped catalog itself --
it is the canonical, committed data this module reads, so the tests exercise it
directly rather than a synthetic stand-in. Mirrors the probe-must-be-able-to-fail
pattern from ops/wq3809-limpet-hal/tests/test_usb_boot_signatures.py (wq#2630).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import target_devices as td  # noqa: E402


def test_loads_every_vendor_file():
    devices = td.load_catalog()
    files = {d.source_file for d in devices}
    assert files == {
        "mediatek", "qualcomm", "rockchip", "amlogic", "allwinner", "samsung",
        "broadcom", "ti-nxp", "apple", "realtek", "hisilicon", "generic-usb",
    }
    assert len(devices) >= 100


def test_every_entry_has_required_fields_and_valid_confidence():
    for d in td.load_catalog():
        assert d.match_type in ("vid_pid", "vid_pid_wildcard", "usb_class")
        assert d.vendor and d.soc_family and d.soc_model and d.mode_name and d.recommended_tool
        assert d.confidence in ("high", "medium", "low")


def test_lookup_finds_qualcomm_edl_in_qualcomm_file():
    hits = td.lookup("05c6", "9008")
    assert any(h.mode_name.startswith("EDL") for h in hits)
    top = hits[0]
    assert top.recommended_tool == "edl / qdl"
    assert top.confidence == "high"
    assert top.source_file == "qualcomm"


def test_lookup_finds_allwinner_fel_in_allwinner_file():
    hits = td.lookup("1f3a", "efe8")
    assert len(hits) == 1
    assert hits[0].source_file == "allwinner"


def test_lookup_matches_oem_wildcard_in_generic_usb_file():
    hits = td.lookup("22b8", "1234")  # arbitrary PID Motorola never specifically listed
    assert any(h.match_type == "vid_pid_wildcard" and h.source_file == "generic-usb" for h in hits)


def test_lookup_matches_usb_class_independent_of_vid():
    hits = td.lookup("ffff", "ffff", class_code="fe:01:00")
    assert any(h.mode_name.startswith("DFU class") for h in hits)


def test_lookup_returns_nothing_for_unlisted_device():
    # Probe drawn from outside the matched population (wq#2630): a VID:PID this
    # catalog does not and should not recognize.
    assert td.lookup("dead", "beef") == []


def test_lookup_sorts_highest_confidence_first():
    hits = td.lookup("05c6", "9008")
    weights = [td.CONFIDENCE_WEIGHT[h.confidence] for h in hits]
    assert weights == sorted(weights, reverse=True)


def test_render_udev_rules_covers_vid_pid_wildcard_and_usb_class():
    devices = td.load_catalog()
    rules = td.render_udev_rules(devices)
    # one vid_pid rule (Qualcomm EDL)
    assert 'ATTR{idVendor}=="05c6", ATTR{idProduct}=="9008"' in rules
    # one vid_pid_wildcard rule (no idProduct match clause)
    assert 'ATTR{idVendor}=="22b8", TAG+="limpet_target"' in rules
    # one usb_class interface-level rule (ADB class signature)
    assert 'ATTRS{bInterfaceClass}=="ff", ATTRS{bInterfaceSubClass}=="42", ATTRS{bInterfaceProtocol}=="01"' in rules
    every_rule_line = [l for l in rules.splitlines() if l.startswith("SUBSYSTEM")]
    assert len(every_rule_line) == len(devices)


def test_checked_in_rules_file_matches_current_catalog():
    # Catches drift: the committed 91-limpet-targets.rules must be regenerated
    # whenever target-devices.d/*.toml changes, not hand-edited out of sync.
    rules_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "target-devices.d", "91-limpet-targets.rules")
    with open(rules_path) as f:
        on_disk = f.read()
    assert on_disk == td.render_udev_rules(td.load_catalog())


# wq#3935 (ewf absorption): per-device flashing profiles + shared partition layouts.

def test_every_rockchip_entry_carries_a_resolvable_profile():
    layouts = td.load_partition_layouts()
    rockchip = [d for d in td.load_catalog() if d.source_file == "rockchip"]
    assert len(rockchip) == 17
    for d in rockchip:
        assert d.recommended_commands, f"{d.usb_vid}:{d.usb_pid} has no recommended_commands"
        assert d.partition_layout in layouts, f"{d.usb_vid}:{d.usb_pid} names unknown layout {d.partition_layout!r}"
        if d.mode_name == "MaskROM":
            # MaskROM has no flash verbs: the playbook must start by chainloading a loader
            assert d.recommended_commands[0].startswith("rkdeveloptool db ")


def test_partition_layouts_have_ascending_offsets_and_a_confidence():
    layouts = td.load_partition_layouts()
    assert set(layouts) >= {"rk-gpt-standard", "rk-legacy-parameter"}
    for name, layout in layouts.items():
        assert layout["confidence"] in ("high", "medium", "low")
        starts = [p["start_sector"] for p in layout["partitions"]]
        assert starts and all(isinstance(s, int) for s in starts)
        assert starts == sorted(starts)


def test_profile_fields_default_empty_outside_the_profiled_population():
    # Probe from outside the population wq#3935 touched (wq#2630): a qualcomm
    # entry must load fine with no profile fields and expose empty defaults.
    hits = td.lookup("05c6", "9008")
    assert hits and hits[0].recommended_commands == ()
    assert hits[0].partition_layout == ""


def test_duplicate_layout_name_across_files_fails_loud(tmp_path):
    (tmp_path / "a.toml").write_text('[partition_layouts.x]\ndescription = "a"\n')
    (tmp_path / "b.toml").write_text('[partition_layouts.x]\ndescription = "b"\n')
    try:
        td.load_partition_layouts(tmp_path)
        assert False, "expected ValueError on a duplicate layout name"
    except ValueError as exc:
        assert "x" in str(exc)
