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
UNCOMMITTED = "ghp" + "_" + "Z3kR8vT1" * 4 + "pQ7w"
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

    # The history index is real git over a real repo. A test that is not about it gets
    # an empty one rather than a minute of this checkout's object store.
    real_history = mask_triage.history_text

    def sandboxed(repo, **kwargs):
        return real_history(repo, **kwargs) if str(repo).startswith(str(tmp_path)) else {}

    monkeypatch.setattr(mask_triage, "history_text", sandboxed)
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


# --- the batch ------------------------------------------------------------------

def test_a_batch_prints_one_line_per_ticket_and_burns_on_the_worst(env, monkeypatch,
                                                                   capsys):
    """wq#2359: 47 tickets off 16 runs. A batch that reported only the last verdict, or
    exited 0 because the last ticket was clean, would close a row holding a real burn."""
    run_dir(env, 4242, "sess-1", "x = '" + PLANTED + "'\n")
    (env / "_runs" / "wq4343" / "snapshots").mkdir(parents=True)
    (env / "_runs" / "wq4343" / "snapshots" / "20260920T222000Z-y.py").write_text(
        "remote: https://" + UNCOMMITTED + "@host/o/r.git\n")
    (env / "events" / "fworker-wq4343-a1.jsonl").write_text(json.dumps(
        {"ts": "2026-09-20T22:19:34Z", "event": "start", "session_id": "sess-2"}) + "\n")
    monkeypatch.setattr(mask_replay, "tracked_text",
                        lambda *a, **k: {"tests/f.py": "y = '" + PLANTED + "'\n"})
    monkeypatch.setattr(mask_triage, "ticket_row", lambda ticket, **k: (
        f"[rotation] burned credential in wq#{4242 if ticket == 9000 else 4343} "
        f"transcript (github)", "2026-09-20 22:15:40", ""))
    code = mask_triage.main(["9000", "9001"])
    out = capsys.readouterr()
    assert code == mask_triage.EXIT_BURN
    assert ("wq#9000 run wq#4242 rules=created@abc12345,head values=1 rotate=0 CLEAN"
            in out.out)
    assert ("wq#9001 run wq#4343 rules=created@abc12345,head values=1 rotate=1 ROTATE"
            in out.out)
    assert "2 tickets: 1 clean, 1 to rotate" in out.out
    assert PLANTED not in out.out + out.err
    assert UNCOMMITTED not in out.out + out.err


def test_a_batch_reads_the_tree_and_each_corpus_once(env, monkeypatch):
    """The whole point of the batch: 47 tickets must not mean 47 tree scans and 47
    corpus loads, which is the cost that dispatched a worker per row instead."""
    run_dir(env, 4242, "sess-1", "x = '" + PLANTED + "'\n")
    trees, corpora = [], []
    real_corpus = mask_replay.corpus
    monkeypatch.setattr(mask_replay, "tracked_text",
                        lambda *a, **k: trees.append(1) or {})
    monkeypatch.setattr(mask_replay, "corpus",
                        lambda source, **k: corpora.append(source) or real_corpus(source))
    mask_triage.main(["9000", "9001", "9002", "--source", "4242",
                      "--created-at", "2026-09-20 22:15:40"])
    assert trees == [1] and corpora == [4242]


def test_a_batch_that_resolves_nothing_never_reads_the_tree(env, monkeypatch):
    """An unresolvable ticket is answered from its row; scanning the tracked tree to
    say so is the expensive half of the run."""
    monkeypatch.setattr(mask_replay, "tracked_text",
                        lambda *a, **k: pytest.fail("batch scanned the tree"))
    assert mask_triage.main(["9000", "9001"]) == mask_triage.EXIT_UNRESOLVED


def test_a_batch_reports_a_failed_control_over_a_burn():
    """Exit 1 says "rotate these". Exit 3 says "no verdict here is evidence" — when
    both are true the batch has to say the second, or the burn list is read as complete."""
    assert mask_triage.batch_exit({0, 1, 2, 3}) == mask_triage.EXIT_CONTROL
    assert mask_triage.batch_exit({0, 1, 2}) == mask_triage.EXIT_BURN
    assert mask_triage.batch_exit({0, 2}) == mask_triage.EXIT_UNRESOLVED
    assert mask_triage.batch_exit({0}) == mask_triage.EXIT_CLEAN


def test_a_batch_line_carries_the_count_and_never_a_value():
    rows = mask_triage.triage(0, RULES, tracked={}, items={"a": "k = " + PLANTED + "\n"})
    line = mask_triage.batch_line(2288, mask_triage.exit_code(rows), 2284, "abc12345ef",
                                  rows)
    assert line == ("wq#2288 run wq#2284 rules=created@abc12345,head values=1 rotate=1 "
                    "ROTATE")
    assert PLANTED not in line


def test_the_source_run_is_parsed_from_the_rotation_title():
    m = mask_triage.SOURCE_RE.search(
        "[rotation] burned credential in wq#2284 transcript (kv-secret)")
    assert m and m.group(1) == "2284"


def test_a_value_keyed_ticket_resolves_its_run_from_the_detail(monkeypatch):
    """wq#2341: a row filed for a burned VALUE names no run in its title — that is what
    lets ten runs share one row. The first of them is in the detail, where the template
    has always written `fired on wq#N`, so triage still has a corpus to replay."""
    monkeypatch.setattr(mask_triage, "ticket_row", lambda ticket, **k: (
        "[rotation] burned credential sha256:20227a136ec3 (github)",
        "2026-09-20 22:15:40",
        "wq#2275 mask tripwire: mask-secrets pattern(s) github fired on wq#2284 "
        "attempt 1's stream-json."))
    assert mask_triage.resolve(9000) == (2284, "2026-09-20 22:15:40")


def test_a_title_that_names_the_run_still_wins(monkeypatch):
    """The backlog's rows carry BOTH: the run in the title and, since every detail is
    written off the same template, a `fired on wq#N` under it. They agree today; if a
    re-filed row ever made them disagree, the title is the one a reader is settling."""
    monkeypatch.setattr(mask_triage, "ticket_row", lambda ticket, **k: (
        "[rotation] burned credential in wq#4242 transcript (github)",
        "2026-09-20 22:15:40", "... fired on wq#9999 attempt 1's stream-json."))
    assert mask_triage.resolve(9000)[0] == 4242


# --- the revision that held it ----------------------------------------------------

def commit_then_drop(tmp_path, body):
    """A repo whose HEAD tree no longer holds `body`, but whose history does."""
    repo = tmp_path / "hist"
    (repo / "harness" / "tests").mkdir(parents=True)
    git(repo, "2026-09-20T22:00:00", "init", "-q")
    fixture = repo / "harness" / "tests" / "test_token_injection.py"
    fixture.write_text(body)
    git(repo, "2026-09-20T22:01:00", "add", "-A")
    git(repo, "2026-09-20T22:01:00", "commit", "-qm", "fixture")
    fixture.write_text("# the fixture was cut\n")
    git(repo, "2026-09-20T22:02:00", "add", "-A")
    git(repo, "2026-09-20T22:02:00", "commit", "-qm", "cut the fixture")
    return repo


def test_a_fixture_cut_from_head_is_still_accounted_by_its_own_revision(
        env, monkeypatch, capsys):
    """wq#2359: 12 of 16 values the backlog called UNACCOUNTED were this — a run read a
    worktree behind main, and the string it read had been committed and then removed."""
    repo = commit_then_drop(env, "SECRET = '" + PLANTED + "'\n")
    run_dir(env, 4242, "sess-1", "x = '" + PLANTED + "'\n")
    monkeypatch.setattr(mask_replay, "tracked_text", lambda *a, **k: {})
    assert cli("--repo", str(repo)) == mask_triage.EXIT_CLEAN
    out = capsys.readouterr().out
    assert "[ACCOUNTED] github" in out and "accounted: history:" in out
    assert PLANTED not in out


def test_a_value_no_revision_ever_held_still_burns(env, monkeypatch, capsys):
    """The history index is a wider excuse, so the burn path has to survive it."""
    repo = commit_then_drop(env, "SECRET = 'unrelated-fixture-text'\n")
    run_dir(env, 4242, "sess-1", "remote: https://" + PLANTED + "@host/o/r.git\n")
    monkeypatch.setattr(mask_replay, "tracked_text", lambda *a, **k: {})
    assert cli("--repo", str(repo)) == mask_triage.EXIT_BURN
    assert "[UNACCOUNTED] github" in capsys.readouterr().out


def test_no_history_scores_against_heads_tree_alone(env, monkeypatch):
    repo = commit_then_drop(env, "SECRET = '" + PLANTED + "'\n")
    run_dir(env, 4242, "sess-1", "x = '" + PLANTED + "'\n")
    monkeypatch.setattr(mask_replay, "tracked_text", lambda *a, **k: {})
    assert cli("--repo", str(repo), "--no-history") == mask_triage.EXIT_BURN


def test_history_is_never_built_for_a_ticket_the_tree_already_explains(env, monkeypatch):
    """The index costs a minute on a real repo. A clean first pass must not pay it."""
    run_dir(env, 4242, "sess-1", "x = '" + PLANTED + "'\n")
    monkeypatch.setattr(mask_replay, "tracked_text",
                        lambda *a, **k: {"tests/f.py": "y = '" + PLANTED + "'\n"})
    monkeypatch.setattr(mask_triage, "history_text",
                        lambda *a, **k: pytest.fail("built the history index"))
    assert cli() == mask_triage.EXIT_CLEAN


def test_the_history_index_is_built_once_for_a_whole_batch(env, monkeypatch):
    run_dir(env, 4242, "sess-1", "remote: https://" + PLANTED + "@host/o/r.git\n")
    monkeypatch.setattr(mask_replay, "tracked_text", lambda *a, **k: {})
    built = []
    monkeypatch.setattr(mask_triage, "history_text",
                        lambda *a, **k: built.append(1) or {})
    monkeypatch.setattr(mask_triage, "ticket_row", lambda ticket, **k: (
        "[rotation] burned credential in wq#4242 transcript (github)",
        "2026-09-20 22:15:40", ""))
    assert mask_triage.main(["9000", "9001", "9002"]) == mask_triage.EXIT_BURN
    assert built == [1]


def test_a_history_index_that_swallows_the_control_fails_the_run(env, monkeypatch,
                                                                 capsys):
    """If the index ever accounts for a value nothing committed — a stale cache, the
    wrong repo — the control catches it before a verdict is printed."""
    run_dir(env, 4242, "sess-1", "remote: https://" + PLANTED + "@host/o/r.git\n")
    monkeypatch.setattr(mask_replay, "tracked_text", lambda *a, **k: {})
    monkeypatch.setattr(mask_triage, "history_text",
                        lambda *a, **k: {"history:deadbeef": "ghp" + "_"})
    monkeypatch.setattr(mask_replay, "account",
                        lambda value, tracked: "history:deadbeef" if tracked else None)
    assert cli() == mask_triage.EXIT_CONTROL
    out = capsys.readouterr()
    assert "CONTROL FAILED" in out.err and "to rotate" not in out.out


def test_history_text_reads_every_reachable_blob_and_skips_the_huge_ones(env):
    repo = commit_then_drop(env, "SECRET = '" + PLANTED + "'\n")
    blobs = mask_triage.history_text(str(repo))
    assert any(PLANTED in text for text in blobs.values())
    assert all(key.startswith("history:") for key in blobs)
    assert mask_triage.history_text(str(repo), max_blob=1) == {}


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
