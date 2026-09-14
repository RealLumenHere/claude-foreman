"""wq#1677 tests — fixtures use the exact stale Description= strings that
were live before the fix (captured from the fleet-units baseline at commit
90d651b, pre-dating the 2026-09-13 snapshot that landed the correction) and
the corrected strings from ingest_decisions.DECISIONS."""
import os

import deploy_descriptions as dd
import ingest_decisions as decisions

STALE_TEXT = {
    "lumen-familiarity.timer": (
        "[Unit]\n"
        "Description=Entity-familiarity ingest every 6h "
        "(installed DISABLED; Frank-gated enable)\n"
        "\n"
        "[Timer]\n"
        "OnCalendar=*-*-* 00/6:00:00\n"
    ),
    "lumen-foreman-dispatch.timer": (
        "[Unit]\n"
        "Description=20-min cadence for lumen-foreman-dispatch "
        "(STAGED — Frank enables; wq#715)\n"
        "\n"
        "[Timer]\n"
        "OnCalendar=*:0/20\n"
    ),
}


def test_rewrite_replaces_stale_description():
    corrected = dict((u, d) for u, _dec, d, _r in decisions.DECISIONS)
    new_text, changed = dd.rewrite_description(
        STALE_TEXT["lumen-familiarity.timer"],
        corrected["lumen-familiarity.timer"],
    )
    assert changed is True
    assert "Description=Entity-familiarity ingest every 6h\n" in new_text
    assert "DISABLED" not in new_text
    assert "OnCalendar=*-*-* 00/6:00:00" in new_text  # rest of unit untouched


def test_rewrite_is_idempotent_once_already_fixed():
    corrected = dict((u, d) for u, _dec, d, _r in decisions.DECISIONS)
    fixed_once, _ = dd.rewrite_description(
        STALE_TEXT["lumen-foreman-dispatch.timer"],
        corrected["lumen-foreman-dispatch.timer"],
    )
    fixed_twice, changed_again = dd.rewrite_description(
        fixed_once, corrected["lumen-foreman-dispatch.timer"]
    )
    assert changed_again is False
    assert fixed_twice == fixed_once


def test_apply_rewrites_only_stale_units(tmp_path):
    for unit, text in STALE_TEXT.items():
        (tmp_path / unit).write_text(text)
    # every other DECISIONS unit is already correct on "disk"
    corrected = dict((u, d) for u, _dec, d, _r in decisions.DECISIONS)
    for unit, desc in corrected.items():
        if unit in STALE_TEXT:
            continue
        (tmp_path / unit).write_text("[Unit]\nDescription=%s\n" % desc)

    changed = dd.apply(str(tmp_path))

    assert set(changed) == set(STALE_TEXT)
    for unit in STALE_TEXT:
        text = (tmp_path / unit).read_text()
        assert ("Description=" + corrected[unit]) in text


def test_apply_is_a_no_op_when_everything_already_matches(tmp_path):
    corrected = dict((u, d) for u, _dec, d, _r in decisions.DECISIONS)
    for unit, desc in corrected.items():
        (tmp_path / unit).write_text("[Unit]\nDescription=%s\n" % desc)

    changed = dd.apply(str(tmp_path))

    assert changed == []
