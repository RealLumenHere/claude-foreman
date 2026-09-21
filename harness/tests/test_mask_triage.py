"""wq#2358: a rotation-ticket triage must be able to report a real burn, and must never
carry the value it reports.

mask_replay's own tests pin the corpus and the hash-only record. What this file pins is
the layer a ticket clears through: the rules are the ones live when the alarm fired,
the verdict comes from burn_filter.classify() and not a private copy of it, the exit
code is non-zero only for a value nothing explains, and — the one that decides whether
the tool is worth having — a planted, never-committed ghp_ + 36 comes back UNACCOUNTED
and still burns. A triage that prints "clean" for a token it cannot see is worse than
the hour it replaces.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import burn_filter
import mask_replay
import mask_triage

# Split at the prefix, like test_mask_replay.py: the shape has to exist at assert time
# without this source line carrying a whole token.
PLANTED = "ghp" + "_" + "Q7xW2pL9" * 4 + "mK4n"
CALLED_IDENT = "        key = apply_key_allowlist(reg)\n"
LEGACY = mask_replay.LEGACY_PATTERNS
HEAD = mask_replay.CURRENT_PATTERNS
RULES = [("created", LEGACY, False), ("head", HEAD, True)]


def run_dir(tmp_path, ticket, session, snapshot_text=None):
    """A run's artefacts: an event stream whose `start` line names the session, and
    (optionally) one snapshot. No episode.jsonl — a killed run never writes one."""
    events = tmp_path / "events"
    (events / "read_cache").mkdir(parents=True)
    (events / f"fworker-wq{ticket}-a1.jsonl").write_text(json.dumps(
        {"ts": "2026-09-20T22:19:34Z", "event": "start", "session_id": session}) + "\n")
    snaps = tmp_path / "_runs" / f"wq{ticket}" / "snapshots"
    snaps.mkdir(parents=True)
    if snapshot_text is not None:
        (snaps / "20260920T221947Z-x.py").write_text(snapshot_text)
    return tmp_path


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setattr(mask_triage, "WQ_DB", str(tmp_path / "absent.db"))
    monkeypatch.setattr(mask_triage, "rules_at",
                        lambda *a, **k: ("abc12345", LEGACY, False))
    return tmp_path


def cli(*extra):
    return mask_triage.main(["9000", "--source", "4242",
                             "--created-at", "2026-09-20 22:15:40", *extra])


def test_the_control_catches_a_planted_token_under_both_rule_sets():
    assert mask_triage.control(RULES, tracked={}, live=set()) is True


def test_a_control_the_classifier_cannot_fail_is_a_failed_control(monkeypatch):
    """If classify() ever calls everything benign, every ticket triages clean. The
    control has to notice that rather than share the blind spot."""
    monkeypatch.setattr(burn_filter, "classify", lambda *a, **k: ([], ["github"]))
    assert mask_triage.control(RULES, tracked={}, live=set()) is False


def test_a_control_whose_rules_cannot_see_the_token_fails():
    blind = [("created", [], False), ("head", HEAD, True)]
    assert mask_triage.control(blind, tracked={}, live=set()) is False


def test_a_planted_token_in_the_corpus_exits_1_and_prints_no_value(env, monkeypatch,
                                                                    capsys):
    run_dir(env, 4242, "sess-1", "remote: https://" + PLANTED + "@host/o/r.git\n")
    monkeypatch.setattr(mask_replay, "tracked_text", lambda *a, **k: {})
    assert cli() == mask_triage.EXIT_BURN
    shown = capsys.readouterr()
    assert "[UNACCOUNTED] github len=36" in shown.out
    assert PLANTED not in shown.out + shown.err
    assert PLANTED[4:] not in shown.out + shown.err


def test_a_committed_value_is_accounted_and_exits_0(env, monkeypatch, capsys):
    run_dir(env, 4242, "sess-1", "x = '" + PLANTED + "'\n")
    monkeypatch.setattr(mask_replay, "tracked_text",
                        lambda *a, **k: {"tests/f.py": "y = '" + PLANTED + "'\n"})
    assert cli() == mask_triage.EXIT_CLEAN
    assert "[ACCOUNTED] github" in capsys.readouterr().out


def test_a_broken_classifier_exits_3_before_printing_a_verdict(env, monkeypatch, capsys):
    run_dir(env, 4242, "sess-1", "x = " + PLANTED + "\n")
    monkeypatch.setattr(mask_replay, "tracked_text", lambda *a, **k: {})
    monkeypatch.setattr(burn_filter, "classify", lambda *a, **k: ([], ["github"]))
    assert cli() == mask_triage.EXIT_CONTROL
    out = capsys.readouterr()
    assert "CONTROL FAILED" in out.err and "to rotate" not in out.out


def test_a_live_credential_is_fatal_even_if_committed():
    """Committed text excuses a fixture, not a value the harness is running on."""
    rows = mask_triage.triage(
        0, RULES, tracked={"f.py": "x = " + PLANTED + "\n"},
        items={"transcript:a": "x = " + PLANTED + "\n"}, live={PLANTED})
    assert [r["verdict"] for r in rows] == ["LIVE"]
    assert mask_triage.exit_code(rows) == mask_triage.EXIT_BURN


def test_a_string_only_the_old_rules_read_is_rule_fixed_and_not_fatal():
    """The dominant class in the backlog: a called identifier the pre-anchoring kv rule
    read as a secret. Nothing commits it, HEAD no longer matches it, no rotation."""
    rows = mask_triage.triage(0, RULES, tracked={},
                              items={"snapshot:a": CALLED_IDENT})
    assert [(r["verdict"], r["rules"]) for r in rows] == [("RULE-FIXED", ["created"])]
    assert mask_triage.exit_code(rows) == mask_triage.EXIT_CLEAN


def test_the_same_value_under_both_tables_is_one_row():
    rows = mask_triage.triage(0, RULES, tracked={},
                              items={"a": PLANTED + "\n", "b": PLANTED + "\n"})
    assert len(rows) == 1 and rows[0]["rules"] == ["created", "head"]


def test_classification_never_puts_a_value_in_a_subprocess(monkeypatch):
    """burn_filter.is_committed_literal() greps with the value in argv, which is fine
    for a fixture and forbidden for a credential. Triage hands it an in-process
    runner, so a value nothing commits must not reach subprocess at all."""
    def boom(*a, **k):
        raise AssertionError("triage spawned a process")
    monkeypatch.setattr(subprocess, "run", boom)
    rows = mask_triage.triage(0, RULES, tracked={}, items={"a": PLANTED + "\n"})
    assert [r["verdict"] for r in rows] == ["UNACCOUNTED"]


def test_no_row_carries_a_value_or_a_line():
    rows = mask_triage.triage(0, RULES, tracked={}, items={"a": "k = " + PLANTED + "\n"})
    assert PLANTED not in json.dumps(rows)
    assert all("line" not in r for r in rows)


def test_the_session_resolves_from_the_event_stream_alone(env):
    """A run killed before attempt_end has no episode.jsonl; its `start` line still
    names the session, and that is what keys events/read_cache/<session>.jsonl."""
    run_dir(env, 4242, "sess-from-start")
    assert not (env / "_runs" / "wq4242" / "episode.jsonl").exists()
    assert mask_replay.session_ids(4242) == ["sess-from-start"]


def test_a_read_cache_window_is_in_the_corpus_via_the_stream_session(env):
    run_dir(env, 4242, "sess-1")
    read = env / "read_me.py"
    read.write_text("filler\nx = " + PLANTED + "\n")
    (env / "events" / "read_cache" / "sess-1.jsonl").write_text(json.dumps(
        {"path": str(read), "offset": "2", "limit": "1"}) + "\n")
    rows = mask_triage.triage(4242, RULES, tracked={}, items=mask_replay.corpus(4242))
    assert [(r["verdict"], r["first_seen_in"].split(":")[0]) for r in rows] == [
        ("UNACCOUNTED", "read")]


def test_exit_2_when_the_run_left_nothing_or_the_ticket_cannot_be_resolved(env):
    assert cli() == mask_triage.EXIT_UNRESOLVED
    assert mask_triage.main(["9000"]) == mask_triage.EXIT_UNRESOLVED


def test_the_source_run_is_parsed_from_the_rotation_title():
    m = mask_triage.SOURCE_RE.search(
        "[rotation] burned credential in wq#2284 transcript (kv-secret)")
    assert m and m.group(1) == "2284"


# --- the rule that was live -----------------------------------------------------

def git(repo, when, *args):
    env = {**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when}
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    *args], check=True, capture_output=True, env=env)


def test_rules_at_loads_the_table_committed_when_the_ticket_was_filed(tmp_path):
    """Anchoring landed mid-cascade: the same ticket scored under HEAD's table alone
    says "no hit" for a hit that was real when it fired."""
    repo = tmp_path / "repo"
    (repo / "harness").mkdir(parents=True)
    git(repo, "2026-09-20T22:00:00", "init", "-q")
    old = 'import re\nPATTERNS = [("sk-key", re.compile(r"(sk-)[a-z]{4}"), r"\\1M")]\n'
    new = ('import re\nPATTERNS = [("sk-key", re.compile(r"\\b(sk-)[a-z]{9}"), r"\\1M")]\n'
           'def _matches(rx, text):\n    return []\n')
    for when, body in (("2026-09-20T22:09:00", old), ("2026-09-20T22:35:00", new)):
        (repo / "harness" / "masking.py").write_text(body)
        git(repo, when, "add", "harness/masking.py")
        git(repo, when, "commit", "-qm", "masking " + when)
    sha, patterns, skip = mask_triage.rules_at("2026-09-20 22:15:40", str(repo))
    assert [label for label, _ in patterns] == ["sk-key"] and skip is False
    assert patterns[0][1].search("sk-abcd")
    _, later, skip_later = mask_triage.rules_at("2026-09-20 23:00:00", str(repo))
    assert skip_later is True and not later[0][1].search("sk-abcd")
    assert mask_triage.rules_at("2026-09-20 21:00:00", str(repo)) is None


def test_rules_at_reproduces_the_frozen_legacy_table_at_its_own_commit():
    """The private copy in mask_replay is documented as harness/masking.py at
    aa52797; loading that commit by date must give the same regexes, or one of the
    two is not evidence."""
    got = mask_triage.rules_at("2026-09-20 22:15:40")
    if got is None or not got[0].startswith("aa52797"):
        pytest.skip("aa52797 is not the revision at that date in this checkout")
    assert [(label, rx.pattern) for label, rx in got[1]] == [
        (label, rx.pattern) for label, rx in LEGACY]
    assert got[2] is False
