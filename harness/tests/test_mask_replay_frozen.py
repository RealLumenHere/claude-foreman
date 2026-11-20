"""wq#5134: mask_replay pins outlive CLI transcript retention.

A pin taken off a transcript used to decay to a skip once the CLI pruned the file or
lumen-mask-at-rest rewrote it (30 skips across 26 tickets by wq#5030). capture_fixture()
now freezes the transcript-sourced replay records (hashes, lengths, masked context,
never a value) into fixtures/mask_replay/wq<N>.json when the pin is written, and
corpus()/replay() answer from it once the transcript is gone or changed.
"""
import glob
import hashlib
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mask_replay  # noqa: E402
import masking  # noqa: E402

TICKET = 990001
SID = "5134aaaa-0000-4000-8000-00000000f1x0".replace("x", "e")
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _value():
    # Built at runtime, never spelled in this file, so the tree cannot account for it
    # and this source never arms the tripwire (the house rule for credential fixtures).
    body = hashlib.sha256(b"wq5134-frozen-pin").hexdigest()[:36]
    return "gh" + "p_" + body


@pytest.fixture
def run(tmp_path, monkeypatch):
    runs, events, projects, fixtures = (tmp_path / d for d in ("runs", "events", "projects", "fx"))
    for d in (runs, events, projects / "-home-lumen-x"):
        d.mkdir(parents=True)
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(runs))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(events))
    monkeypatch.setattr(mask_replay, "PROJECTS_ROOT", str(projects))
    monkeypatch.setattr(mask_replay, "FIXTURE_ROOT", str(fixtures))
    (events / f"fworker-wq{TICKET}-1.jsonl").write_text(
        json.dumps({"event": "start", "ts": "2026-11-19T12:00:00Z", "session_id": SID}) + "\n")
    transcript = projects / "-home-lumen-x" / f"{SID}.jsonl"
    line = {"type": "user", "message": {"content": "printf '%s' " + "export GH_" + "TOKEN=" + _value()}}
    transcript.write_text(json.dumps({"type": "summary"}) + "\n" + json.dumps(line) + "\n")
    return transcript


def _shas(records):
    return {r["value_sha256_12"] for r in records}


def _want():
    return hashlib.sha256(_value()[4:].encode()).hexdigest()[:12]


def test_capture_writes_records_and_no_value(run):
    path = mask_replay.capture_fixture(TICKET, tracked={})
    text = open(path).read()
    assert _value() not in text and _value()[4:] not in text
    assert masking.mask_text(text)[1] == []
    doc = json.loads(text)
    assert doc["sessions"] == [SID]
    for table in ("legacy", "current"):
        assert _want() in _shas(doc["records"][table]), table
        assert all(r["first_seen_in"] == f"transcript:{SID}" for r in doc["records"][table])


@pytest.mark.parametrize("fate", ["pruned", "masked-at-rest"])
def test_pin_survives_the_transcript(run, fate):
    live = mask_replay.replay(TICKET, patterns=mask_replay.CURRENT_PATTERNS, tracked={}, rotated={})
    assert _want() in _shas(live)
    mask_replay.capture_fixture(TICKET, tracked={})
    if fate == "pruned":
        run.unlink()
    else:   # what lumen-mask-at-rest does: same file, value gone
        run.write_text(run.read_text().replace(_value()[4:], masking.MASK))
    items = mask_replay.corpus(TICKET)
    assert f"frozen:wq{TICKET}" in items
    after = mask_replay.replay(TICKET, patterns=mask_replay.CURRENT_PATTERNS, tracked={},
                               items=items, rotated={})
    assert _shas(after) == _shas(live)
    frozen = [r for r in after if r.get("frozen")]
    assert frozen and all(r["first_seen_in"] == f"frozen:transcript:{SID}" for r in frozen)
    assert len(mask_replay.unaccounted(after)) == len(mask_replay.unaccounted(live))
    legacy = mask_replay.replay(TICKET, tracked={}, items=items, rotated={})
    assert _want() in _shas(legacy)


def test_unchanged_transcript_adds_no_frozen_item(run):
    mask_replay.capture_fixture(TICKET, tracked={})
    assert f"frozen:wq{TICKET}" not in mask_replay.corpus(TICKET)


def test_other_tables_get_nothing_from_a_fixture(run):
    mask_replay.capture_fixture(TICKET, tracked={})
    run.unlink()
    items = mask_replay.corpus(TICKET)
    custom = [("github", re.compile(r"(gh[a-z]_)[A-Za-z0-9]{6,}"))]
    assert mask_replay.replay(TICKET, patterns=custom, tracked={}, items=items, rotated={}) == []
    assert mask_replay.suppressed_records(TICKET, items=items) == []


def test_capture_needs_a_live_transcript(run):
    run.unlink()
    with pytest.raises(mask_replay.FixtureError):
        mask_replay.capture_fixture(TICKET, tracked={})
    assert mask_replay.main(["capture-fixture", str(TICKET)]) == 2


# --- review lint -------------------------------------------------------------------
# Pins that read a transcript call _skip_if_masked_at_rest (directly or through
# _assert_corpus_pinned). Every such ticket needs a committed fixture, except the ones
# pinned before fixtures existed, whose transcripts were already pruned when wq#5134
# landed (not in any backup either: ~/.claude/projects is deliberately not backed up).
# This list may only SHRINK. A new pin goes through `mask_replay.py capture-fixture N`.
PRE_FIXTURE_PINS = frozenset({
    2120, 2253, 2278, 2300, 2410, 2417, 2687, 2702, 2720, 2787, 2848, 2913, 2922,
    3021, 3036, 3037, 3059, 3076, 3078, 3134, 3169, 3216, 3343, 3753, 4244, 4260,
})
PIN_CALL = re.compile(r"_(?:skip_if_masked_at_rest|assert_corpus_pinned)\(\s*(\d+)")


def _pinned_tickets():
    found = set()
    for path in glob.glob(os.path.join(TESTS_DIR, "test_mask_replay*.py")):
        with open(path) as fh:
            found |= {int(n) for n in PIN_CALL.findall(fh.read())}
    return found


def test_every_transcript_pin_has_a_fixture():
    real_root = os.path.join(TESTS_DIR, "fixtures", "mask_replay")
    missing = sorted(t for t in _pinned_tickets() - PRE_FIXTURE_PINS
                     if mask_replay.load_fixture(t, root=real_root) is None)
    assert missing == [], (
        f"transcript-sourced pin(s) without a frozen fixture: {missing}. Run "
        f"`python3 harness/mask_replay.py capture-fixture <N>` while the transcript "
        f"still exists and commit fixtures/mask_replay/wq<N>.json with the pin (wq#5134)")


def test_pre_fixture_list_only_shrinks():
    real_root = os.path.join(TESTS_DIR, "fixtures", "mask_replay")
    stale = sorted(t for t in PRE_FIXTURE_PINS if mask_replay.load_fixture(t, root=real_root))
    assert stale == [], f"{stale} now have fixtures: drop them from PRE_FIXTURE_PINS"
    assert PRE_FIXTURE_PINS <= _pinned_tickets() | PRE_FIXTURE_PINS


def test_committed_fixtures_carry_no_credential():
    for path in glob.glob(os.path.join(TESTS_DIR, "fixtures", "mask_replay", "*.json")):
        with open(path) as fh:
            text = fh.read()
        assert masking.mask_text(text)[1] == [], path
        assert masking._orphan_matches(text) == [], path
        doc = json.loads(text)
        assert set(doc["records"]) == {"legacy", "current"}, path
