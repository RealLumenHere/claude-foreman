"""wq#4048 -- the check that would have caught wq#3972's unlabelled tshark entry."""

import pathlib

import pytest

from limpet import license_policy as lp

CATALOG = pathlib.Path(__file__).parent / "tool-catalog.json"


def test_live_catalog_has_no_policy_violations():
    assert lp.check_catalog(lp.load_tools(CATALOG)) == []


@pytest.mark.parametrize("name", ["tshark", "afl++", "ghidra"])
def test_bundled_core_tools_declare_both_fields(name):
    entry = next(t for t in lp.load_tools(CATALOG) if t["name"] == name)
    assert entry["license_risk"] in lp.LICENSE_RISKS
    assert entry["redistribution_posture"] in lp.REDISTRIBUTION_POSTURES


def test_tshark_is_classified_copyleft_with_a_source_offer():
    # GPL-2.0 into a commercially-sold ISO is the case wq#3959's policy exists for.
    entry = next(t for t in lp.load_tools(CATALOG) if t["name"] == "tshark")
    assert entry["license"] == "GPL-2.0"
    assert entry["license_risk"] == "copyleft-source-offer"


@pytest.mark.parametrize(
    "name", ["qemu-user", "qemu-system", "gdb-multiarch", "flashrom", "openocd",
             "sigrok-cli", "radare2"]
)
def test_gpl_bundling_tools_state_a_source_offer_obligation(name):
    # wq#4050: the trust=core tools wq#4048's judgment flagged as high-probability
    # copyleft must land classified as copyleft, bundled, and with the ISO's
    # source-offer obligation spelled out in provenance -- not just a bare enum value.
    entry = next(t for t in lp.load_tools(CATALOG) if t["name"] == name)
    assert entry["license_risk"] == "copyleft-source-offer"
    assert entry["redistribution_posture"] == "bundle"
    assert "source offer" in entry["provenance"]


def test_new_entry_without_the_fields_is_a_violation():
    # The ratchet: a name outside the frozen legacy backlog must carry both fields.
    problems = lp.check_entry({"name": "brand-new-tool", "license": "GPL-3.0"})
    assert len(problems) == 1
    assert "missing license_risk and redistribution_posture" in problems[0]


def test_legacy_unaudited_backlog_is_cleared():
    # wq#4050: the 41 pre-policy entries are all audited now; the ratchet baseline
    # frozen by wq#4048 shrinks to empty rather than ever gaining a name.
    assert lp.LEGACY_UNAUDITED == frozenset()


def test_legacy_entry_without_the_fields_is_exempt(monkeypatch):
    # LEGACY_UNAUDITED is empty on the live catalog (wq#4050), so this exercises
    # the exemption mechanism itself against a synthetic legacy name rather than
    # a real one -- there is no real one left to exercise it against.
    monkeypatch.setattr(lp, "LEGACY_UNAUDITED", frozenset({"some-legacy-tool"}))
    assert lp.check_entry({"name": "some-legacy-tool", "license": "MIT"}) == []
    assert "some-legacy-tool" in lp.LEGACY_UNAUDITED


def test_partial_declaration_names_only_the_missing_field():
    problems = lp.check_entry({"name": "halfway", "license_risk": "none"})
    assert problems == [
        "halfway: missing redistribution_posture "
        "(tool-catalog.json provenance.license_risk_policy, wq#3959)"
    ]


@pytest.mark.parametrize(
    "entry, expected",
    [
        ({"name": "t", "license_risk": "copyleft", "redistribution_posture": "bundle"},
         "license_risk='copyleft'"),
        ({"name": "t", "license_risk": "none", "redistribution_posture": "ship-it"},
         "redistribution_posture='ship-it'"),
    ],
)
def test_out_of_enum_values_are_rejected(entry, expected):
    problems = lp.check_entry(entry)
    assert len(problems) == 1
    assert expected in problems[0]


def test_legacy_exemption_does_not_excuse_a_bad_value(monkeypatch):
    # Exempt from "must declare", not from "declare it correctly".
    monkeypatch.setattr(lp, "LEGACY_UNAUDITED", frozenset({"some-legacy-tool"}))
    problems = lp.check_entry({"name": "some-legacy-tool", "license_risk": "mit-ish"})
    assert len(problems) == 1
    assert "license_risk='mit-ish'" in problems[0]


@pytest.mark.parametrize(
    "posture, bundleable",
    [
        ("bundle", True),
        ("external-invoke", False),
        ("blocked-pending-permission", False),
        (None, True),
    ],
)
def test_is_bundleable_follows_the_posture(posture, bundleable):
    entry = {"name": "t"}
    if posture is not None:
        entry["redistribution_posture"] = posture
    assert lp.is_bundleable(entry) is bundleable


def test_cli_exit_code_is_zero_on_the_live_catalog(capsys):
    assert lp.main(["--catalog", str(CATALOG)]) == 0
    assert "0 license-policy violation(s)" in capsys.readouterr().out


def test_cli_exit_code_is_nonzero_when_a_violation_exists(tmp_path, capsys):
    bad = tmp_path / "catalog.json"
    bad.write_text(
        '{"tools": [{"name": "brand-new-tool", "license": "GPL-3.0"}]}',
        encoding="utf-8",
    )
    assert lp.main(["--catalog", str(bad)]) == 1
    assert "brand-new-tool: missing" in capsys.readouterr().out
