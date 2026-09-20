"""wq#2307: the rotation-ticket replay must name a value without carrying it, and must
reproduce the OLD alarm rather than today's.

A rotation ticket asks one question — was a live credential in that run's model
context? mask_replay answers it from the run's own artefacts. Two properties decide
whether the answer can be trusted: the legacy table has to still fire on the shapes
that filed the backlog (a tool scored under today's tighter rules would report "no
hit" for every open ticket and prove nothing), and no record may carry a value, since
the records land in a PR body and an ISSUE-LOG row.

The live pin at the bottom is wq#2307's own verdict, kept executable: wq#2288's
transcript held 13 credential-shaped strings and every one of them is committed
source.
"""
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mask_replay
import masking

# The three shapes the backlog is actually made of (ISSUE-LOG rows 568-571): a called
# identifier, a masked artefact read back, and a test fixture.
CALLED_IDENT = "        key = apply_key_allowlist(reg)\n"
OWN_OUTPUT = 'PAT: github_pat_MASKED and ghp_MASKED\n'
FIXTURE = 'GITEA_TOKEN="hunter2-hunter2-hunter2"\n'
LIVE = 'export GITEA_TOKEN="Zq7xW2pL9mK4nR8tY6vB3cF5hJ1dS0aG"\n'


def test_legacy_rules_fire_on_a_called_identifier_and_current_rules_do_not():
    """wq#2288's shape. If the legacy table were dropped in favour of the live one,
    every ticket filed before 22:35 would replay clean for the wrong reason."""
    legacy = list(mask_replay._hits(mask_replay.LEGACY_PATTERNS, CALLED_IDENT,
                                    skip_masked_output=False))
    current = list(mask_replay._hits(mask_replay.CURRENT_PATTERNS, CALLED_IDENT,
                                     skip_masked_output=True))
    assert [h[0] for h in legacy] == ["kv-secret"]
    assert current == []


def test_legacy_rules_re_arm_on_masked_output_and_current_rules_do_not():
    """wq#2298: `ghp_MASKED` was itself credential-shaped, so a worker grepping its own
    run dir re-armed the alarm. The replay must reproduce that, or the tickets it
    caused look unexplained."""
    legacy = list(mask_replay._hits(mask_replay.LEGACY_PATTERNS, OWN_OUTPUT,
                                    skip_masked_output=False))
    current = list(mask_replay._hits(mask_replay.CURRENT_PATTERNS, OWN_OUTPUT,
                                     skip_masked_output=True))
    assert {h[0] for h in legacy} == {"github", "kv-secret"}
    assert current == []


def test_a_committed_value_is_accounted_and_an_unknown_one_is_not():
    tracked = {"tests/test_daemon.py": FIXTURE}
    records = mask_replay.replay(0, tracked=tracked,
                                 items={"snapshot:a": FIXTURE + LIVE})
    by_hash = {r["value_sha256_12"]: r for r in records}
    assert len(records) == 2
    fixture_rec = by_hash[hashlib.sha256(
        b"hunter2-hunter2-hunter2").hexdigest()[:12]]
    assert fixture_rec["accounted_in"] == "tests/test_daemon.py:1"
    loose = mask_replay.unaccounted(records)
    assert [r["value_len"] for r in loose] == [32]
    assert loose[0]["prefix"] == 'TOKEN="'


def test_a_closed_pattern_defect_is_not_a_burn():
    """The dominant backlog shape: a called identifier inside a worktree edit that
    never landed, so nothing in the tree matches it verbatim. Screening on the tree
    alone would call it a burned credential and bill another rotate-today ticket;
    today's table not matching the string at all is the second, decisive screen."""
    records = mask_replay.replay(0, tracked={}, items={"snapshot:a": CALLED_IDENT})
    assert [r["still_matches_today"] for r in records] == [False]
    assert records[0]["accounted_in"] is None
    assert mask_replay.unaccounted(records) == []


def test_a_value_in_neither_screen_is_reported(tmp_path):
    records = mask_replay.replay(0, tracked={}, items={"snapshot:a": LIVE})
    assert records[0]["still_matches_today"] is True
    assert mask_replay.unaccounted(records) == records


def test_a_record_carries_no_value_anywhere():
    """The records are quoted into ticket notes and PR bodies. Prefix, length, 12 hex
    of sha256 and a spliced line identify a credential; none of them use it."""
    records = mask_replay.replay(0, tracked={}, items={"snapshot:a": LIVE})
    blob = json.dumps(records)
    assert "Zq7xW2pL9mK4nR8tY6vB3cF5hJ1dS0aG" not in blob
    assert records[0]["line"] == 'export GITEA_TOKEN="<kv-secret 32 chars>"'
    assert records[0]["value_sha256_12"] == hashlib.sha256(
        b"Zq7xW2pL9mK4nR8tY6vB3cF5hJ1dS0aG").hexdigest()[:12]


def test_the_same_value_in_two_artefacts_is_one_record():
    """One ticket per burned value is the goal (wq#2341); a replay that counted the
    same fixture once per snapshot would argue for the opposite."""
    records = mask_replay.replay(0, tracked={}, items={"snapshot:a": FIXTURE,
                                                       "snapshot:b": FIXTURE,
                                                       "events:c": FIXTURE})
    assert len(records) == 1
    assert records[0]["first_seen_in"] == "snapshot:a"


def write_run(tmp_path, ticket, session):
    run = tmp_path / "_runs" / f"wq{ticket}"
    (run / "snapshots").mkdir(parents=True)
    (run / "snapshots" / "20260920T000001Z-x.py").write_text(CALLED_IDENT)
    (run / "episode.jsonl").write_text(
        json.dumps({"type": "attempt_end", "session_id": session}) + "\n")
    events = tmp_path / "events"
    (events / "read_cache").mkdir(parents=True)
    (events / f"fworker-wq{ticket}-a1.jsonl").write_text(OWN_OUTPUT)
    read = tmp_path / "read_me.py"
    read.write_text("filler\n" + FIXTURE)
    (events / "read_cache" / f"{session}.jsonl").write_text(
        json.dumps({"path": str(read), "offset": "2", "limit": "1"}) + "\n")
    return run


def test_corpus_covers_snapshots_run_records_events_and_the_read_cache(tmp_path,
                                                                       monkeypatch):
    """The read cache is the only source that names a file the run merely READ and
    never wrote — which is where every fixture-shaped hit in the backlog came from."""
    write_run(tmp_path, 4242, "sess-1")
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    items = mask_replay.corpus(4242)
    kinds = {k.split(":", 1)[0] for k in items}
    assert kinds == {"snapshot", "run", "events", "read"}
    # the read-cache entry re-cut the window, not the whole file
    read_item = next(v for k, v in items.items() if k.startswith("read:"))
    assert read_item == FIXTURE
    records = mask_replay.replay(4242, tracked={}, items=items)
    assert {r["label"] for r in records} == {"kv-secret", "github"}


def test_cli_exits_2_when_the_run_left_nothing_to_replay(tmp_path, monkeypatch):
    """Exit 2, never 0: "no artefacts" must not read as "nothing was burned"."""
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    assert mask_replay.main(["4242"]) == 2


def test_cli_exit_code_separates_a_burn_from_a_fixture(tmp_path, monkeypatch):
    write_run(tmp_path, 4242, "sess-1")
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setattr(mask_replay, "tracked_text", lambda *a, **k: {})
    assert mask_replay.main(["4242"]) == 1
    monkeypatch.setattr(
        mask_replay, "tracked_text",
        lambda *a, **k: {"src.py": CALLED_IDENT + OWN_OUTPUT + FIXTURE})
    assert mask_replay.main(["4242"]) == 0


WQ2288_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2288")


@pytest.mark.skipif(not os.path.isdir(WQ2288_RUN),
                    reason="wq#2288's run dir is not on this host")
def test_wq2288_transcript_burned_nothing():
    """wq#2307's verdict, pinned. The tripwire filed a pri-1 rotate-today ticket on
    wq#2288's stream at 22:28:27; replaying that run's own artefacts under the rules
    live at 22:28 finds 13 credential-shaped strings and accounts for every one of
    them in a committed file, so nothing was rotated. If this ever fails, some value
    that run saw has since LEFT the tree, and it needs a fresh look."""
    records = mask_replay.replay(2288)
    assert records, "wq#2288's artefacts should still reproduce the hit"
    assert mask_replay.unaccounted(records) == []
    assert "kv-secret" in {r["label"] for r in records}


@pytest.mark.skipif(not os.path.isdir(WQ2288_RUN),
                    reason="wq#2288's run dir is not on this host")
def test_the_live_rules_would_not_have_filed_wq2307():
    """The leak path, closed: of the 13, the kv-secret values that fired are called
    identifiers (`token = read_oauth_token()`), which masking.py stopped reading as
    credentials 12 minutes after this ticket was filed."""
    records = mask_replay.replay(2288, patterns=mask_replay.CURRENT_PATTERNS)
    assert mask_replay.unaccounted(records) == []
    assert len(records) < 13
    # what still matches is synthetic fixture text, which is burn_filter's screen
    assert all("tests/test_" in r["accounted_in"]
               for r in records if r["label"] == "kv-secret")


def test_legacy_table_is_not_the_live_one():
    """A future tightening of masking.PATTERNS must not silently rewrite history."""
    assert mask_replay.LEGACY_PATTERNS is not mask_replay.CURRENT_PATTERNS
    assert [p[0] for p in mask_replay.LEGACY_PATTERNS] != [
        p[0] for p in mask_replay.CURRENT_PATTERNS] or any(
        legacy[1].pattern != current[1].pattern
        for legacy, current in zip(mask_replay.LEGACY_PATTERNS,
                                   mask_replay.CURRENT_PATTERNS))
    assert masking.MASK == "MASKED"
