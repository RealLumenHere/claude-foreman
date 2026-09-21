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
import io
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mask_replay
import masking

# The three shapes the backlog is actually made of (ISSUE-LOG rows 568-571): a called
# identifier, a masked artefact read back, and a test fixture.
CALLED_IDENT = "        key = apply_key_allowlist(reg)\n"
OWN_OUTPUT = 'PAT: github_pat_MASKED and ghp_MASKED\n'
# wq#2356: split between `TOKEN="` and the body — the boundary the kv rule reads, and
# `'` is not in its body charset — so this file's own source stops arming the tripwire.
# The concatenation leaves the runtime bytes identical, so the replay still sees a whole
# value in the items below, which is the half of the fixture the tests actually assert on.
FIXTURE = 'GITEA_TOKEN="' + 'hunter2-hunter2-hunter2"\n'
LIVE = 'export GITEA_TOKEN="' + 'Zq7xW2pL9mK4nR8tY6vB3cF5hJ1dS0aG"\n'


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


def test_a_fixture_split_to_dodge_the_tripwire_still_accounts():
    """wq#2326: the two defences have to agree. A credential-shaped fixture is split
    across a concatenation so that reading its source stops filing rotation tickets
    (345bf49), but the runtime value still reaches the transcript whole — so the
    screen that asks "is this value committed?" must read the source the way Python
    does. The body below straddles the seam, which is the only case that breaks: it
    needs `ant-` from the left fragment to reach the 16-char floor, so a verbatim
    find fails and the dummy reports BURNED?, paging a human to rotate a fixture."""
    split = 'OAT_PADDED = "sk-ant-" + "oat01-padded"\n'
    records = mask_replay.replay(0, tracked={"tests/test_token_injection.py": split},
                                 items={"transcript:a": 'v = "sk-ant-' + 'oat01-padded"'})
    assert [r["value_len"] for r in records] == [16]
    assert records[0]["accounted_in"] == "tests/test_token_injection.py:1"
    assert mask_replay.unaccounted(records) == []


def test_closing_a_seam_cannot_move_a_line_number_or_invent_a_value():
    """The seam pass rewrites the tree it reports line numbers from, so it is only
    safe while it leaves newlines alone. It must also stay conservative about what
    counts as a seam: a comma or a colon between two literals is a list or a dict,
    not a concatenation, and joining those would manufacture a value no runtime ever
    produces — an accounting that says "committed" for a live credential."""
    tree = {"f.py": '\n\n\nA = "sk-ant-" + "oat01-padded"\n'}
    assert mask_replay.account("ant-oat01-padded", tree) == "f.py:4"
    for gap in ('", "', '": "', '" , "'):
        pair = {"f.py": 'x = ["sk-ant-%soat01-padded"]\n' % gap}
        assert mask_replay.account("ant-oat01-padded", pair) is None, gap


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


# --- re-cutting a read from the bytes the model saw --------------------------

def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c",
                    "user.name=t", *args], check=True, capture_output=True)


def read_record(path, offset, limit, size):
    return json.dumps({"path": str(path), "offset": str(offset),
                       "limit": str(limit), "fp": f"{size}:1:1.1"}) + "\n"


def test_a_read_of_an_unchanged_file_is_recut_live(tmp_path, monkeypatch):
    run = write_run(tmp_path, 4242, "sess-1")
    read = tmp_path / "read_me.py"
    (tmp_path / "events" / "read_cache" / "sess-1.jsonl").write_text(
        read_record(read, 2, 1, read.stat().st_size))
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(run.parent))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    items = mask_replay.corpus(4242)
    assert next(v for k, v in items.items() if k.startswith("read:")) == FIXTURE


def test_a_changed_file_is_recut_from_the_revision_it_was_read_at(tmp_path,
                                                                  monkeypatch):
    """wq#2324: replaying wq#2291 off the live tree attributed two sk-key values to
    comment lines written 18 minutes AFTER the alarm, in a file that had grown from
    2137B to 11693B — the run's own edits to the masker it was investigating. The
    recorded size names the revision that held the bytes actually read."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    src = repo / "src.py"
    src.write_text("filler\n" + FIXTURE)
    was = src.stat().st_size
    git(repo, "add", "src.py")
    git(repo, "commit", "-qm", "as read")
    src.write_text("filler\n" + CALLED_IDENT + "a comment added after the run\n")
    assert src.stat().st_size != was

    run = write_run(tmp_path, 4242, "sess-1")
    (tmp_path / "events" / "read_cache" / "sess-1.jsonl").write_text(
        read_record(src, 2, 1, was))
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(run.parent))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    items = mask_replay.corpus(4242)
    assert next(v for k, v in items.items() if k.startswith("read:")) == FIXTURE


def test_a_window_whose_bytes_are_gone_is_marked_stale(tmp_path, monkeypatch):
    """No revision ever held that size, so the window is today's text standing in for
    what the run read. It is reported, and it neither demands nor excuses a rotation:
    counting it as a burn would rotate a live credential over a line written after
    the fact, and counting it as accounted would hide one."""
    run = write_run(tmp_path, 4242, "sess-1")
    read = tmp_path / "read_me.py"
    (tmp_path / "events" / "read_cache" / "sess-1.jsonl").write_text(
        read_record(read, 2, 1, read.stat().st_size + 4096))
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(run.parent))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    items = mask_replay.corpus(4242)
    assert [k for k in items if k.startswith("read")][0].startswith("read-stale:")
    records = mask_replay.replay(4242, tracked={}, items=items)
    stale = [r for r in records if not r["faithful"]]
    assert [r["label"] for r in stale] == ["kv-secret"]
    assert stale[0] not in mask_replay.unaccounted(records)
    out = io.StringIO()
    mask_replay.report(4242, records, out=out)
    assert "[re-cut]" in out.getvalue()


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


# wq#2311: what a shell command printed. Split at the prefix, like test_masking.py —
# the shape has to survive to assert time without the source line carrying it.
PRINTED = "remote: https://" + "ghp_" + "Kq3mZ8" + "x" * 30 + "@github.com/o/r.git\n"
TWO_ON_A_LINE = ("env: GH=" + "ghp_" + "A" * 36 + " SK=" + "sk-ant-" + "b" * 24 + "\n")


def write_transcript(tmp_path, worktree, session, text):
    """The CLI's layout: one directory per project, slugged from the worktree path."""
    proj = tmp_path / "projects" / worktree.replace("/", "-")
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{session}.jsonl").write_text(json.dumps({"text": text}) + "\n")
    return tmp_path / "projects"


def test_a_value_only_a_command_printed_is_found_in_the_transcript(tmp_path,
                                                                   monkeypatch):
    """wq#2311's gap. Masking runs before any artefact write, so a token in a Bash
    stdout is on disk only as `<prefix>MASKED` — no snapshot, no Read, nothing for the
    other three sources to find. Without the transcript this run replays clean and the
    tool signs off on a burn."""
    write_run(tmp_path, 4242, "sess-1")
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setattr(mask_replay, "PROJECTS_ROOT", str(tmp_path / "nowhere"))
    without = mask_replay.replay(4242, tracked={})
    assert not [r for r in without if r["prefix"] == "ghp_"]

    root = write_transcript(tmp_path, "/home/lumen/worktrees/wq4242-a1", "sess-1",
                            PRINTED)
    monkeypatch.setattr(mask_replay, "PROJECTS_ROOT", str(root))
    with_it = mask_replay.replay(4242, tracked={})
    printed = [r for r in with_it if r["prefix"] == "ghp_"]
    assert [r["first_seen_in"] for r in printed] == ["transcript:sess-1"]
    assert printed[0]["value_len"] == 36 and printed[0]["still_matches_today"]
    assert printed[0] in mask_replay.unaccounted(with_it)


def test_a_transcript_record_masks_the_rest_of_its_line(tmp_path, monkeypatch):
    """A raw line can hold two credentials. Splicing only the value the record is
    about would print the other one into a ticket — the leak the whole tool exists to
    avoid. Artefact lines are already masked, so this pass only ever bites here."""
    write_run(tmp_path, 4242, "sess-1")
    root = write_transcript(tmp_path, "/home/lumen/worktrees/wq4242-a1", "sess-1",
                            TWO_ON_A_LINE)
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setattr(mask_replay, "PROJECTS_ROOT", str(root))
    records = mask_replay.replay(4242, tracked={})
    line = next(r["line"] for r in records if r["prefix"] == "ghp_")
    assert "<github 36 chars>" in line
    assert "b" * 24 not in line and "sk-ant-" in line and masking.MASK in line


def test_a_hit_deep_in_a_transcript_line_reports_the_text_around_it():
    """wq#2317: one transcript line is one JSONL record — tens of KB of it — so a
    window cut from the line's HEAD names the record's parentUuid and never reaches the
    value. `line` is the field that says WHERE the credential printed from, which on
    this run is what separated eight burns from eight literals a worker typed into its
    own Edit call. A line short enough to fit is still reported whole (the assertion in
    test_a_record_carries_no_value_anywhere pins that)."""
    record = ('{"parentUuid":"8d50dd6d","message":{"model":"claude-opus-5","content":'
              '[{"type":"text","text":"' + "filler " * 200 + 'ran: ' + LIVE.strip()
              + '"}]}}\n')
    records = mask_replay.replay(0, tracked={}, items={"transcript:sess-1": record})
    line = records[0]["line"]
    assert "ran: export GITEA_TOKEN=" in line and "<kv-secret 32 chars>" in line
    assert "parentUuid" not in line and line.startswith("…")


def test_the_transcript_does_not_re_attribute_what_an_artefact_explains(tmp_path,
                                                                        monkeypatch):
    """Ordered last for a reason: a hit an artefact can name keeps that name, because
    `snapshot:<file>` tells the next reader where to look and `transcript:<id>` does
    not."""
    write_run(tmp_path, 4242, "sess-1")
    root = write_transcript(tmp_path, "/home/lumen/worktrees/wq4242-a1", "sess-1",
                            CALLED_IDENT)
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setattr(mask_replay, "PROJECTS_ROOT", str(root))
    records = mask_replay.replay(4242, tracked={})
    assert [r["first_seen_in"] for r in records
            if r["label"] == "kv-secret"][0].startswith("snapshot:")


WQ2288_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2288")
WQ2299_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2299")


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


@pytest.mark.skipif(not os.path.isdir(WQ2299_RUN),
                    reason="wq#2299's run dir is not on this host")
def test_wq2299_transcript_burned_nothing():
    """wq#2326's verdict, pinned, under BOTH tables — the run is the one whose own
    subject was the tripwire, so its corpus holds every dummy in
    test_token_injection.py plus the PR prose explaining the bug. The three values
    that filed wq#2326 are `task-engine-daemon-restart`, `risk-assessment-matrix-2026`
    and `disk-usage-report-generator`, which is prose, and the fourth is the split
    `OAT_PADDED` fixture. Nothing was rotated. A failure here means a value this run
    saw has left the tree, or the seam pass stopped reading the split fixtures."""
    for table in (None, mask_replay.CURRENT_PATTERNS):
        records = mask_replay.replay(2299, patterns=table)
        assert records, "wq#2299's artefacts should still reproduce the hit"
        assert mask_replay.unaccounted(records) == []
    # asserted against that one file, not against the whole tree: this test module
    # now quotes the same split literal, and `account` returns the first tracked
    # file holding the value — which sorts ahead of the fixture it is vouching for.
    fixture = "harness/tests/test_token_injection.py"
    with open(os.path.join(mask_replay.REPO_ROOT, fixture)) as fh:
        assert mask_replay.account("ant-oat01-padded",
                                   {fixture: fh.read()}) == f"{fixture}:24"


WQ2253_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2253")

# The three `github` values wq#2253 attempt 1 put in front of the model, named the way
# a settled rotation ticket is allowed to name one: 12 hex of sha256, and a length.
# Bodies of 6, 6 and 8 characters — a live forge token's body is 36 — because all three
# are doctrine-log prose about THIS false-positive class: an open-source solver's repo
# name with a four-character forge prefix sitting mid-word (rows 568/570/574), this
# module's own masked output quoted back (row 571), and the broker's tripwire probe
# string. Spelled as hashes rather than as text on purpose, twice over: a record must
# never carry its value, and the sentence that spells the mid-word prefix is the one
# the PR broker refused to post in row 588.
WQ2253_GITHUB = {"b8789db0c2da": 6, "20227a136ec3": 6, "d354caf95d3e": 8}

# The three `sk-key` values from the same attempt — wq#2337, the third rotate-today
# ticket this one read filed — named the same way, and worth naming separately because
# the blanket `unaccounted` check passes whether they are present or absent.
# Every body is exactly 16 characters, the legacy rule's own `{16,}` floor: each cleared
# the bar by nothing. They are not keys but the tails of ordinary hyphenated English in
# the doctrine log's row about THIS false-positive class, caught where a word happens to
# end in the two letters before a hyphen — unanchored, the rule could not tell that from
# a prefix. Spelled as hashes for the two reasons WQ2253_GITHUB is: a record must never
# carry its value, and a sentence that spells one is what row 588's broker refused.
WQ2253_SK_KEY = {"24d32cfa0c9d": 16, "157fdb29bd0d": 16, "78bcca60d790": 16}

# The gate snapshot whose frozen bytes carried all three into context, written
# 22:51:23 — one second before the alarm filed wq#2337.
WQ2253_DOCTRINE_SNAPSHOT = "snapshot:20260920T225123Z-home_lumen_kb_ISSUE-LOG.md"


@pytest.mark.skipif(not os.path.isdir(WQ2253_RUN),
                    reason="wq#2253's run dir is not on this host")
def test_wq2253_transcript_burned_nothing():
    """wq#2335's verdict, pinned — and those of its twins wq#2314 (`kv-secret`) and
    wq#2337 (`sk-key`), which the tripwire filed against this same attempt. Three pri-1
    rotate-today tickets off one run; replaying its artefacts under the rules live at
    22:32 reproduces 9 credential-shaped strings and accounts for every one of them in a
    committed file, so nothing was rotated. A failure here means a value this run saw
    has left the tree and deserves a fresh look — not that a credential was burned."""
    records = mask_replay.replay(2253)
    assert records, "wq#2253's artefacts should still reproduce the hit"
    assert mask_replay.unaccounted(records) == []
    github = {r["value_sha256_12"]: r for r in records if r["label"] == "github"}
    assert {h: r["value_len"] for h, r in github.items()} == WQ2253_GITHUB
    # the load-bearing half: `unaccounted` also forgives a value today's table stopped
    # reading, and today's table reads none of these, so it would pass vacuously.
    assert all(r["accounted_in"] for r in github.values())
    sk_key = {r["value_sha256_12"]: r for r in records if r["label"] == "sk-key"}
    assert {h: r["value_len"] for h, r in sk_key.items()} == WQ2253_SK_KEY
    assert all(r["accounted_in"] for r in sk_key.values())
    # wq#2337's leak path, pinned to an artefact rather than argued from timing: all
    # three first reach the model in one gated read of the doctrine log. Snapshots sort
    # first in the corpus and this run's other 19 are its own worktree files, so the
    # source of these values is the log — not anything the attempt was editing.
    assert {r["first_seen_in"] for r in sk_key.values()} == {WQ2253_DOCTRINE_SNAPSHOT}


@pytest.mark.skipif(not os.path.isdir(WQ2253_RUN),
                    reason="wq#2253's run dir is not on this host")
def test_the_live_rules_would_not_have_filed_wq2335():
    """The leak path, closed, and closed harder than wq#2307's: the whole corpus of
    this run scores ZERO under today's table, not merely fewer. Both defects that fired
    it were shipped before the ticket was — the word boundary (wq#2292) stops a prefix
    being read mid-word, and the 30-character body floor (same) stops a 6-character
    doctrine-log quote being read as a token at all — yet the attempt kept paging under
    the table it had imported at spawn, which is the hole wq#2327 later closed.

    Zero is per-corpus, not per-label, so this settles wq#2337's `sk-key` and wq#2314's
    `kv-secret` on the same evidence: the boundary is what stops a mid-word prefix in
    hyphenated prose, the one defect all three tickets share."""
    assert mask_replay.replay(2253, patterns=mask_replay.CURRENT_PATTERNS) == []


@pytest.mark.skipif(not os.path.isdir(WQ2253_RUN),
                    reason="wq#2253's run dir is not on this host")
def test_wq2253_sk_key_is_absent_from_the_model_context():
    """The assertion wq#2337's ticket text actually asks for, which no other test here
    makes. `first_seen_in` cannot answer it — snapshots sort first in the corpus, so it
    names where a value is EARLIEST, not where it is only. Scored source by source, all
    three values live in the gate's full-file snapshot of the doctrine log and NOWHERE
    else: the CLI transcript, 1.5MB of the model's context verbatim, carries none of
    them, and the one doctrine-log read the run made returned a four-line window
    starting one line below the row that holds them.

    A snapshot freezes the whole file, a Read returns a window, and this alarm paged on
    the difference — so the ticket's premise ("the value reached the model context") is
    false here quite apart from the values being committed prose. Kept as a test rather
    than a log row because it is the only cheap way to tell the two apart on the NEXT
    alarm: a value in the transcript reached the model, a value only in a snapshot did
    not, and the second class costs an xhigh worker an hour to re-derive."""
    items = mask_replay.corpus(2253)
    tracked = mask_replay.tracked_text()
    found = {}
    for source, text in items.items():
        hits = {r["value_sha256_12"] for r
                in mask_replay.replay(2253, tracked=tracked, items={source: text})
                if r["label"] == "sk-key"}
        if hits:
            found[source] = hits
    assert found == {WQ2253_DOCTRINE_SNAPSHOT: set(WQ2253_SK_KEY)}
    assert [s for s in items if s.startswith("transcript:")], \
        "the model's own context must be in the corpus for the above to mean anything"
    reads = [s for s in items if "kb/ISSUE-LOG.md@" in s]
    assert [s.rsplit("@", 1)[1] for s in reads] == ["658"]


WQ2300_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2300")

# The two `kv-secret` values mask-hits-1.json recorded for wq#2300 attempt 1, named as
# a settled rotation ticket is allowed to name one: 12 hex of sha256, and a length.
# Neither is a credential. The 17 is this masker's own output quoted back out of a
# docstring explaining the rule — a forge prefix plus the literal the substitution
# writes — and the 23 is the triple-`hunter2` joke fixture, which is why the bodies are
# 17 and 23 where a live forge token's is 36. Spelled as hashes because a record must
# never carry its value, and because a file that spelled them would re-arm the alarm it
# is vouching for — the same reason the fixtures themselves are written split.
WQ2300_KV = {"2c9e04df04aa": 17, "ae71c4cdf5d7": 23}


@pytest.mark.skipif(not os.path.isdir(WQ2300_RUN),
                    reason="wq#2300's run dir is not on this host")
def test_wq2300_transcript_burned_nothing():
    """wq#2336's verdict, pinned — and its twin wq#2332's, which settled the same
    attempt's `cloudflare` and `sk-key` labels (row 596). wq#2300 was the run that FIXED
    the sk- prefix bug, so it spent the attempt reading the masker's own source, its
    fixtures and the doctrine rows describing earlier links of this chain; replaying its
    artefacts under the rules live at 22:48–23:10 reproduces 20 credential-shaped
    strings and accounts for every one in a committed file, so nothing was rotated. A
    failure here means a value this run saw has left the tree and deserves a fresh look
    — not that a credential was burned."""
    records = mask_replay.replay(2300)
    assert records, "wq#2300's artefacts should still reproduce the hit"
    assert mask_replay.unaccounted(records) == []
    kv = {r["value_sha256_12"]: r for r in records if r["label"] == "kv-secret"}
    assert WQ2300_KV.items() <= {h: r["value_len"] for h, r in kv.items()}.items()
    # the load-bearing half: `unaccounted` also forgives a value today's table stopped
    # reading, and it stopped reading the 17, so that half would pass vacuously.
    assert all(kv[h]["accounted_in"] for h in WQ2300_KV)


@pytest.mark.skipif(not os.path.isdir(WQ2300_RUN),
                    reason="wq#2300's run dir is not on this host")
def test_the_live_rules_would_not_have_filed_wq2336():
    """The leak path, closed, and closed in two places rather than one. The table shut
    the masker's own output out: the 17 no longer matches any rule, because kv-secret's
    value stopped ending on a masked body. The 23 still matches and always will — it is
    a fixture whose whole point is to be credential-shaped — so what stops it is the
    second screen, `burn_filter`, which pages only on a value it cannot find in a
    committed file. Both shipped before this ticket was filed; the attempt kept paging
    under the table it had imported at spawn, the hole wq#2327 closed."""
    records = mask_replay.replay(2300, patterns=mask_replay.CURRENT_PATTERNS)
    assert mask_replay.unaccounted(records) == []
    assert len(records) < 20
    kv = {r["value_sha256_12"]: r for r in records if r["label"] == "kv-secret"}
    assert "2c9e04df04aa" not in kv
    # what still matches is synthetic fixture text, which is burn_filter's screen
    assert all("tests/test_" in r["accounted_in"] for r in kv.values())


WQ2278_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2278")
WQ2278_A1 = "docs/wq2278-token-engine-t10-remaining-verbs-and-mig-a1"

# The single `cloudflare` value wq#2278 attempt 1 put in front of the model, named the
# way a settled rotation ticket is allowed to name one: 12 hex of sha256, and a length.
# A live Cloudflare token's body is 40 characters; this body is 6 because it is this
# masker's own output read back — the substitution literal behind a `cfut_` prefix,
# quoted in the comment that explains this very class at harness/burn_filter.py:84.
# The test asserts the hash against masking.MASK rather than spelling the string, for
# the reason the fixtures elsewhere in this file are written split.
WQ2278_CLOUDFLARE = {"20227a136ec3": 6}

# What accounts nowhere in main's tree: gateway-key fixtures the attempt TYPED, in the
# shape it typed them. The PR renamed and split them before it landed — the survivor is
# `"sk-" + "minted" * 5` at token-engine/tests/test_daemon.py:480 — so a value the run
# wrote itself is absent from the committed tree and reports as BURNED?. That is the
# wq#2318 direction of the same gap: the snapshots hold pre-images, and only the
# transcript records what the Edit put there.
WQ2278_BRANCH_FIXTURES = {
    "840881e18cbe": "token-engine/tests/test_daemon.py",
    "78a2d56fb7d2": "harness/tests/test_gateway_key_per_ticket.py",
    "df9937e539d8": "harness/tests/test_gateway_key_per_ticket.py",
}


def _blob(branch, rel):
    """One file as `branch` has it, or None when the branch is gone from this host."""
    p = subprocess.run(["git", "-C", mask_replay.REPO_ROOT, "show", f"{branch}:{rel}"],
                       capture_output=True, text=True)
    return p.stdout if p.returncode == 0 and p.stdout else None


@pytest.mark.skipif(not os.path.isdir(WQ2278_RUN),
                    reason="wq#2278's run dir is not on this host")
def test_wq2278_cloudflare_hit_was_the_maskers_own_output():
    """wq#2343's verdict, pinned: nothing Cloudflare-shaped was burned and nothing was
    rotated. The tripwire filed a pri-1 rotate-today ticket at 22:59:54Z for a label
    whose whole value is the six characters this module substitutes — the masked
    artefact re-arming the rule that masked it, which is the loop masking.py:186-200
    describes. wq#2290 and wq#2289 settled the same attempt's other two labels.

    A failure here means the value behind that label is no longer the mask literal, and
    the run deserves a fresh look."""
    records = mask_replay.replay(2278)
    assert records, "wq#2278's artefacts should still reproduce the hit"
    cloudflare = {r["value_sha256_12"]: r for r in records if r["label"] == "cloudflare"}
    assert {h: r["value_len"] for h, r in cloudflare.items()} == WQ2278_CLOUDFLARE
    rec = cloudflare["20227a136ec3"]
    assert rec["value_sha256_12"] == hashlib.sha256(
        masking.MASK.encode()).hexdigest()[:12]
    # committed text, and text today's table has stopped reading as a credential at all
    assert rec["accounted_in"]
    assert not rec["still_matches_today"]


@pytest.mark.skipif(not os.path.isdir(WQ2278_RUN),
                    reason="wq#2278's run dir is not on this host")
def test_wq2278_unaccounted_values_are_fixtures_off_its_own_branch():
    """The other half of the same verdict. Replayed against main alone this run reports
    unaccounted values, which is what a rotate-today page is supposed to mean; every one
    of them is a dummy the attempt wrote into its own test files, so adding those files
    to the tracked corpus drives the count to zero under both tables. Nothing here was
    reachable as a credential either way: wq#2278 ran claude-fable-5, which is absent
    from LOCAL_POOL_MODELS (fworker.py:159), so build_env handed it the OAuth token and
    never a gateway key."""
    branch = {}
    for rel in sorted(set(WQ2278_BRANCH_FIXTURES.values())):
        text = _blob(WQ2278_A1, rel)
        if text is None:
            pytest.skip(f"{WQ2278_A1} is no longer on this host")
        branch[f"{WQ2278_A1}:{rel}"] = text
    items, tracked = mask_replay.corpus(2278), mask_replay.tracked_text()
    for patterns in (mask_replay.LEGACY_PATTERNS, mask_replay.CURRENT_PATTERNS):
        loose = mask_replay.unaccounted(
            mask_replay.replay(2278, patterns=patterns, tracked=tracked, items=items))
        assert {r["value_sha256_12"] for r in loose} <= set(WQ2278_BRANCH_FIXTURES)
        with_branch = mask_replay.replay(2278, patterns=patterns, items=items,
                                         tracked=dict(tracked, **branch))
        assert mask_replay.unaccounted(with_branch) == []
        accounted = {r["value_sha256_12"]: r["accounted_in"] for r in with_branch}
        for sha, rel in WQ2278_BRANCH_FIXTURES.items():
            if sha in accounted:
                assert accounted[sha].startswith(f"{WQ2278_A1}:{rel}:")


@pytest.mark.skipif(not os.path.isdir(WQ2278_RUN),
                    reason="wq#2278's run dir is not on this host")
def test_the_live_rules_would_not_have_filed_wq2343():
    """The leak path, closed. The cloudflare rule still reads `cfut_` + six legal
    characters, and `MASKED` is six legal characters, so what stops the masker vouching
    for its own output is masking.py:200 dropping any match that ends in the literal.
    With that in place the label this ticket is named for has no record left in the
    run's whole corpus — there is nothing for it to fire on twice."""
    records = mask_replay.replay(2278, patterns=mask_replay.CURRENT_PATTERNS)
    assert not [r for r in records if r["label"] == "cloudflare"]
    assert masking.mask_text("cfut_" + masking.MASK)[1] == []
    # and the rule is still armed for a body that is not the literal
    assert masking.mask_text("cfut_" + "0" * 24)[1] == ["cloudflare"]


def test_legacy_table_is_not_the_live_one():
    """A future tightening of masking.PATTERNS must not silently rewrite history."""
    assert mask_replay.LEGACY_PATTERNS is not mask_replay.CURRENT_PATTERNS
    assert [p[0] for p in mask_replay.LEGACY_PATTERNS] != [
        p[0] for p in mask_replay.CURRENT_PATTERNS] or any(
        legacy[1].pattern != current[1].pattern
        for legacy, current in zip(mask_replay.LEGACY_PATTERNS,
                                   mask_replay.CURRENT_PATTERNS))
    assert masking.MASK == "MASKED"
