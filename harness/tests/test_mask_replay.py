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


def test_a_seam_after_a_literal_ending_in_a_double_quote_is_closed():
    """wq#2358: token-engine/tests/test_daemon.py:338 splits `TOKEN="` from its body,
    so the left literal ENDS in a `"`. The first seam pattern took that embedded `"'`
    pair as the seam and left the real ` + ` standing: the body alone accounted, the
    whole string did not, and burn_filter.classify() (which asks about the whole
    match) called a committed fixture unaccounted."""
    left, body = 'TOKEN="', 'abcdefgh12345678'
    tree = {"f.py": "x = '" + left + "' + '" + body + "\"'\n"}
    assert mask_replay.account(left + body, tree) == "f.py:1"


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


WQ1819_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq1819")

# The one `kv-secret` value wq#1819 attempt 8 put in front of its model. Unlike every
# other pin in this file the identity is not asserted as a bare hash constant: the
# value is a PUBLIC PYTHON IDENTIFIER, so the test can name it outright and hash it
# back, which is a strictly stronger pin. A bare constant only says "unchanged since
# someone looked"; this says what the string IS, and fails loudly if a future rule ever
# re-reads a different string into this slot.
WQ1819_KV_IDENTIFIER = b"read_oauth_token"


@pytest.mark.skipif(not os.path.isdir(WQ1819_RUN),
                    reason="wq#1819's run dir is not on this host")
def test_wq1819_transcript_burned_nothing():
    """wq#2347's verdict, pinned. The ticket reads `kv-secret fired on wq#1819 attempt
    8's stream-json ... rotate it today`; nothing was rotated, because the "credential"
    is the name of a function.

    The alarm matched `token = read_oauth_token()` at harness/fworker.py:1542 — label
    `token =`, body `read_oauth_token`, 16 characters, which clears the legacy rule's
    12-character floor. The legacy kv rule had no tail lookahead, so it could not tell
    an assignment's VALUE from the head of a CALL, and a call to a function whose job
    is to read a token is the single most credential-shaped line a codebase can hold.

    Attempt 8 ran `nephthys-qwen14b` (attempt-8.json), a local model, and was killed on
    wall-clock after 7 turns — so the bytes reached a model on this host and no further,
    which is not a mitigation but is worth recording next to the verdict."""
    records = mask_replay.replay(1819)
    assert mask_replay.unaccounted(records) == []
    assert len(records) == 1
    kv, = records
    assert kv["label"] == "kv-secret"
    assert kv["value_len"] == len(WQ1819_KV_IDENTIFIER) == 16
    assert kv["value_sha256_12"] == hashlib.sha256(
        WQ1819_KV_IDENTIFIER).hexdigest()[:12]
    # a call, not a value: the recorded line keeps the `()` the rule failed to look at
    assert kv["line"].endswith("()")
    # committed source, hence public to every worker, hence not a secret
    assert kv["accounted_in"].startswith("despag/backends/claude_max.py:")


@pytest.mark.skipif(not os.path.isdir(WQ1819_RUN),
                    reason="wq#1819's run dir is not on this host")
def test_the_live_rules_would_not_have_filed_wq2347():
    """The leak path, closed, and closed twice. kv-secret's tail lookahead (wq#2288)
    ends a match on `(?![A-Za-z0-9._-]|\\()`, so a call can no longer be read as a
    value and the whole corpus now scores zero. Were it ever to match again, the second
    screen holds independently: `burn_filter` pages only on a value it cannot find in a
    committed file, and this one is in two.

    Zero is per-corpus rather than per-label, so this also forecloses the sibling
    tickets that would otherwise be filed against this run under the other labels."""
    assert mask_replay.replay(1819, patterns=mask_replay.CURRENT_PATTERNS) == []
    assert mask_replay.replay(1819)[0]["still_matches_today"] is False


def test_the_kv_tail_lookahead_separates_a_call_from_a_value():
    """The one-line rule change that settles wq#2347, exercised directly rather than
    only through wq#1819's corpus — the corpus pins the verdict, this pins the REASON,
    and would survive the run dir being pruned off the host.

    The lookahead must not buy its silence by going blind to the assignment it is there
    to catch, so both halves are asserted: a call is ignored, the same label with a real
    value is still masked."""
    current = dict(mask_replay.CURRENT_PATTERNS)["kv-secret"]
    assert not current.search("        token = read_oauth_token()")
    assert current.search('        token = "' + "A" * 36 + '"')
    # and the legacy table genuinely did fire on it — otherwise the pin above proves
    # nothing about why the ticket was filed
    legacy = dict(mask_replay.LEGACY_PATTERNS)["kv-secret"]
    assert legacy.search("        token = read_oauth_token()")


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
        loose_shas = {r["value_sha256_12"] for r in loose}
        assert loose_shas <= set(WQ2278_BRANCH_FIXTURES)
        with_branch = mask_replay.replay(2278, patterns=patterns, items=items,
                                         tracked=dict(tracked, **branch))
        assert mask_replay.unaccounted(with_branch) == []
        accounted = {r["value_sha256_12"]: r["accounted_in"] for r in with_branch}
        # Pin the branch file only for a value main cannot explain on its own.
        # `account` returns the FIRST committed file holding a string, so the moment
        # main grows its own copy of one of these dummies — e5b6c92 landed one in
        # ops/wq2316-mask-secrets-boundary/ — the branch corpus stops winning the
        # lookup and this loop was asserting the outcome of a race. The verdict is
        # unharmed, and strictly stronger: the value is committed text either way.
        for sha, rel in WQ2278_BRANCH_FIXTURES.items():
            if sha in accounted and sha in loose_shas:
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


WQ2297_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2297")

# The five distinct `sk-key` values in wq#2297 attempt 1's model context, named the way
# a settled rotation ticket may name one: 12 hex of sha256, and a length. Every one is
# 16 bytes where a live `sk-ant-` key's body is far longer, because none of them is a
# key — they are this masker's own fixtures and the doctrine prose describing this
# chain, quoted back by a run whose subject WAS the sk- rule. Spelled as hashes because
# a record must never carry its value, and because a file that spelled them would
# re-arm the alarm it is vouching for.
WQ2297_SK = {"fb20d2f4274f": 16, "f50f2d7b79dd": 16, "24d32cfa0c9d": 16,
             "157fdb29bd0d": 16, "78bcca60d790": 16}


@pytest.mark.skipif(not os.path.isdir(WQ2297_RUN),
                    reason="wq#2297's run dir is not on this host")
def test_wq2297_transcript_burned_nothing():
    """wq#2366's verdict, pinned. wq#2297 was itself filed by the tripwire on the
    wq#2290 session investigating an earlier link of this chain (row 571), and its own
    attempt then spent 56 minutes editing masking.py and test_masking.py — so it read
    credential-shaped fixtures for the length of the run and re-filed against itself,
    wq#2327, wq#2356 and wq#2366 among them. Replaying the run's artefacts under the
    rules live at 22:37 reproduces 24 credential-shaped strings and accounts for every
    one in a committed file, so nothing was rotated. A failure here means a value this
    run saw has since LEFT the tree and deserves a fresh look — not that a credential
    was burned."""
    records = mask_replay.replay(2297)
    assert records, "wq#2297's artefacts should still reproduce the hit"
    assert mask_replay.unaccounted(records) == []
    sk = {r["value_sha256_12"]: r["value_len"]
          for r in records if r["label"] == "sk-key"}
    assert WQ2297_SK.items() <= sk.items()


@pytest.mark.skipif(not os.path.isdir(WQ2297_RUN),
                    reason="wq#2297's run dir is not on this host")
def test_the_live_rules_would_not_have_filed_wq2366():
    """The leak path, closed at the pattern. Every `sk-key` here came from the
    unanchored `sk-[a-zA-Z-]*` shape reading ordinary hyphenated prose as a key —
    `disk-usage-…` lands as prefix `sk-usage-`, and `sk-an…` is the rule backtracking
    into its own masked body. Today's table reads none of them, so the label that filed
    wq#2366 produces nothing at all; what survives the run is two strings out of
    test_masking.py's own fixture table, which burn_filter screens on provenance rather
    than shape. Both shipped before wq#2366 was filed — the attempt kept paging under
    the table it imported at spawn, the hole wq#2327 closed."""
    records = mask_replay.replay(2297, patterns=mask_replay.CURRENT_PATTERNS)
    assert mask_replay.unaccounted(records) == []
    assert [r for r in records if r["label"] == "sk-key"] == []
    assert all("tests/test_" in r["accounted_in"] for r in records)


# The one `slack` value mask-hits-1.json recorded for wq#2300 attempt 1, named the only
# way a settled rotation ticket may name one: 12 hex of sha256, and a length. It is the
# `xoxb-` fixture from harness/tests/test_masking.py — a body of two dash-joined groups
# counting up through the digits and the head of the alphabet, where a live bot token
# carries three. Spelled as a hash because a file that spelled it would re-arm the alarm
# it is vouching for, which is the whole shape of the chain these rows keep closing.
WQ2300_SLACK = ("2af11783433b", 19)


@pytest.mark.skipif(not os.path.isdir(WQ2300_RUN),
                    reason="wq#2300's run dir is not on this host")
def test_wq2300_slack_label_burned_nothing():
    """wq#2354's verdict, pinned — the third and last label off wq#2300 attempt 1, after
    row 596's `cloudflare,sk-key` (wq#2332) and row 605's `kv-secret` (wq#2336). The
    attempt was fixing the sk- prefix bug in the masker's own fixture file, where this
    `xoxb-` value still sat WHOLE in SECRETS, so reading it put the fixture in front of
    the model.

    Two things make this record different from its siblings. Every artefact the run
    wrote holds `xoxb-MASKED` and nothing else, so the transcript is the only source
    that can both prove the value reached the model and name it (wq#2311) — pin it, or
    a corpus that lost the transcript would report this run clean by going blind. And
    the value still matches today's table: `unaccounted` forgives either a committed
    value or one the rules stopped reading, and only the first screen applies here, so
    the accounting is load-bearing rather than vacuous.
    """
    records = mask_replay.replay(2300)
    slack = [r for r in records if r["label"] == "slack"]
    assert [(r["value_sha256_12"], r["value_len"]) for r in slack] == [WQ2300_SLACK]
    assert slack[0]["first_seen_in"].startswith("transcript:")
    assert slack[0]["accounted_in"].startswith("harness/tests/test_masking.py:")
    assert slack[0]["still_matches_today"]


@pytest.mark.skipif(not os.path.isdir(WQ2300_RUN),
                    reason="wq#2300's run dir is not on this host")
def test_the_live_rules_would_not_have_filed_wq2354():
    """The leak path, closed 2m10s after the ticket was filed and by the same run: the
    alarm fired 23:10:03Z, wq#2300 filed this at 23:10:04Z, and 74dcb82 (23:12:14Z)
    split the SECRETS entry that fired so it interpolates the already-split constant.
    What holds that split in place is wq#2356's guard in test_masking.py —
    `test_the_mask_suite_does_not_arm_its_own_tripwire` — which reads the whole mask
    suite's source and requires zero hits. It is upstream of everything below.

    Downstream, the live table alone would not have saved it — a fixture whose whole
    point is to be credential-shaped still matches at HEAD. What stops the page is
    burn_filter's screen on a value a committed file explains.
    """
    records = mask_replay.replay(2300, patterns=mask_replay.CURRENT_PATTERNS)
    slack = [r for r in records if r["label"] == "slack"]
    assert [(r["value_sha256_12"], r["value_len"]) for r in slack] == [WQ2300_SLACK]
    assert slack[0]["accounted_in"]
    assert mask_replay.unaccounted(records) == []


WQ2348_LABELS = ("cloudflare", "github", "kv-secret")
WQ2348_TRANSCRIPT = "transcript:bc57c139-6203-424b-b273-ed24a7053fe5"

# The ten values wq#2278 ATTEMPT 2 put in front of the model under the three labels that
# filed wq#2348, named the way a settled rotation ticket is allowed to name one: 12 hex
# of sha256, and a length. Three are this masker's own substitution literal read back —
# behind a `cfut_` prefix, behind a forge one, and a third time with the forge prefix
# inside the value the kv rule keeps (6, 6 and 17). Six are Python the attempt read:
# `os.path.join`, `os.environ.get`, and the names of four called functions
# (`read_oauth_token`, `_ebay_access_token`, `apply_key_allowlist`, and the tail of an
# open-source solver's repo name behind a forge prefix). The tenth is the joke fixture
# wq#2336's row names, quoted out of this very file. A live Cloudflare token's body is 40
# characters and a forge token's is 36; the longest here is 23.
WQ2348_ATTEMPT_2 = {
    ("cloudflare", "20227a136ec3"): 6,
    ("github", "20227a136ec3"): 6,
    ("github", "b8789db0c2da"): 6,
    ("kv-secret", "2c9e04df04aa"): 17,
    ("kv-secret", "eb522505a0e3"): 19,
    ("kv-secret", "166e2282ed20"): 16,
    ("kv-secret", "00ef0d630506"): 18,
    ("kv-secret", "ceb969f320cb"): 14,
    ("kv-secret", "e6e7cef12b65"): 12,
    ("kv-secret", "ae71c4cdf5d7"): 23,
}


@pytest.mark.skipif(not os.path.isdir(WQ2278_RUN),
                    reason="wq#2278's run dir is not on this host")
def test_wq2278_attempt_2_alarm_burned_nothing():
    """wq#2348's verdict, pinned: three labels, ten values, no credential, no rotation.

    The twin of wq#2343 one attempt later — the tripwire filed that ticket for attempt 1
    at 22:59:54Z and this one for attempt 2 at 23:01:44Z, 110 seconds apart, off the same
    run and sharing the `cloudflare` label. The per-attempt alarm set resets at the
    attempt boundary while the corpus that fires it does not: a retry re-reads the same
    committed source and pages again.

    Scored against attempt 2's OWN transcript rather than the run's whole corpus, because
    that is the premise the ticket asserts — that the value reached the model context. It
    did; what reached it was this module's output and three identifiers.

    A failure here means one of these values has left the tree, and the run deserves a
    fresh look."""
    items = mask_replay.corpus(2278)
    assert WQ2348_TRANSCRIPT in items, "attempt 2's own context must be in the corpus"
    records = mask_replay.replay(2278,
                                 items={WQ2348_TRANSCRIPT: items[WQ2348_TRANSCRIPT]})
    got = {(r["label"], r["value_sha256_12"]): r
           for r in records if r["label"] in WQ2348_LABELS}
    assert {k: r["value_len"] for k, r in got.items()} == WQ2348_ATTEMPT_2
    # every one of them is committed text, so burn_filter's screen would hold it benign
    assert all(r["accounted_in"] for r in got.values())
    # the two the masker wrote itself, tied to the literal rather than spelled
    mask = hashlib.sha256(masking.MASK.encode()).hexdigest()[:12]
    prefixed = hashlib.sha256(("github_pat_" + masking.MASK).encode()).hexdigest()[:12]
    assert {("cloudflare", mask), ("github", mask),
            ("kv-secret", prefixed)} <= set(WQ2348_ATTEMPT_2)


@pytest.mark.skipif(not os.path.isdir(WQ2278_RUN),
                    reason="wq#2278's run dir is not on this host")
def test_the_live_rules_would_not_have_filed_wq2348():
    """The leak path, closed before the ticket was filed and closed in three places, one
    per label. The mask literal stopped vouching for itself (masking.py:200, 22:54:46);
    the prefix stopped being read mid-word (wq#2292, 22:35:49); and a called function
    stopped being read as a stored value, which is the `(?!...|\\()` tail of the kv rule.
    Today the three labels have nothing left to fire on in the whole of attempt 2's
    context.

    It fired anyway at 23:01:44Z because the process scoring the stream had imported its
    rule table at 22:14:43Z, when attempt 1 spawned, and held it across the attempt
    boundary — the hole wq#2327 closed for the table. The ticket text is the receipt: it
    carries the pre-22:40 template that 32aa000 replaced 21 minutes before it was
    written.

    One of the ten still matches and always will — the joke fixture, whose whole point is
    to be credential-shaped — so what stops it is the second screen, burn_filter, which
    pages only on a value it cannot find in a committed file."""
    items = mask_replay.corpus(2278)
    records = mask_replay.replay(2278, patterns=mask_replay.CURRENT_PATTERNS,
                                 items={WQ2348_TRANSCRIPT: items[WQ2348_TRANSCRIPT]})
    live = [r for r in records if r["label"] in WQ2348_LABELS]
    assert {r["value_sha256_12"] for r in live} == {"ae71c4cdf5d7"}
    assert all("tests/test_" in r["accounted_in"] for r in live)
    for own_output in ("cfut_" + masking.MASK, "github_pat_" + masking.MASK):
        assert masking.mask_text(own_output)[1] == []
    assert masking.mask_text("hi" + "ghs_solver")[1] == []
    # and each rule is still armed for a body that is not one of those
    assert masking.mask_text("github_pat_" + "0" * 24)[1] == ["github"]
    assert masking.mask_text("ghs_" + "0" * 36)[1] == ["github"]


WQ2316_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2316")
# The one value of wq#2316's 26 that no committed file explains: 16 characters behind
# `xoxb-`, two lowercase English dictionary words with a hyphen between them (11 + 4,
# checked against /usr/share/dict/words without printing it). A live slack bot token's
# body is two long digit runs and a 24-character base62 blob, ~40 characters; nothing
# shaped like this is one, and it appears in no live env var and in no file under any
# directory the run was granted.
WQ2316_SLACK = ("d902a2b4f628", 16)
WQ2316_DRAFT = ("snapshot:20260921T032623Z-home_lumen_worktrees_wq2316-a1_ops_"
                "wq2316-mask-secrets-boundary_test_mask_secrets_boundary.py")


@pytest.mark.skipif(not os.path.isdir(WQ2316_RUN),
                    reason="wq#2316's run dir is not on this host")
def test_wq2316_slack_hit_was_a_fixture_the_run_dropped():
    """wq#2400's verdict, pinned: 26 values, no credential, no rotation.

    The one record no committed file accounts for is the only interesting one, and it is
    the rarest shape in this backlog — a fixture that was drafted and then DELETED before
    the branch landed. The run was writing the parametrize list in
    ops/wq2316-mask-secrets-boundary/test_mask_secrets_boundary.py that asserts prefixes
    inside a word are untouched; the entry it drafted for `xox` masked instead of passing,
    because the boundary spelled `_` as a delimiter, so the entry was dropped and the
    landed file keeps the `cfut_` case alone. `account()` therefore has nothing to match:
    the snapshot holds a line the tree never received. What settles it is the second
    screen — today's table does not read that line as a credential at all (wq#2400 put
    `_` in the boundary class), which is the same screen that settles a called identifier.

    A failure here means the value has appeared in the tree, or the boundary regressed.
    """
    records = mask_replay.replay(2316)
    assert len(records) == 26
    slack = [r for r in records if r["value_sha256_12"] == WQ2316_SLACK[0]]
    assert [(r["label"], r["value_len"]) for r in slack] == [("slack", WQ2316_SLACK[1])]
    assert slack[0]["first_seen_in"] == WQ2316_DRAFT
    assert slack[0]["accounted_in"] is None, "a dropped draft is in no committed file"
    assert not slack[0]["still_matches_today"]
    assert mask_replay.unaccounted(records) == []


@pytest.mark.skipif(not os.path.isdir(WQ2316_RUN),
                    reason="wq#2316's run dir is not on this host")
def test_the_live_rules_would_not_have_filed_wq2400():
    """The leak path, closed in the boundary both copies of the house set share.

    wq#2292 anchored the prefixes at `(?<![A-Za-z0-9])` because `ghs_` inside the repo
    name `highs_solver` had cost a rotation ticket. That class survived one separator
    away: `_` continues an identifier, so `notes_on_xoxb-…` and `my_cfut_settings…` still
    read as tokens and still paged pri-1. Three of this run's labels come from that file
    (`cloudflare,kv-secret,slack`), and the value the alarm was about is two English
    words in a test fixture.

    The rules stay armed for a value the identifier actually LABELS, which is the shape a
    real leak takes — the kv-secret rule keys on the English word and never reads this
    boundary at all.
    """
    records = mask_replay.replay(2316, patterns=mask_replay.CURRENT_PATTERNS)
    assert mask_replay.unaccounted(records) == []
    assert WQ2316_SLACK[0] not in {r["value_sha256_12"] for r in records}
    for tail in ("see notes_on_xoxb-plainword", "reading my_cfut_settings_override",
                 "log_sk-report-generator-2026"):
        assert masking.mask_text(tail)[1] == [], tail
    # Bodies unique to this file on purpose: account() closes a same-line concatenation
    # seam (wq#2326), so reusing test_masking.py's XOXB here would make THIS file the
    # first tracked explanation of wq#2300's slack value and move that ticket's pin.
    for label, live in (("slack", "xoxb-" + "9876543210-wq2400bound"),
                        ("cloudflare", "cfut_" + "v1.0-" + "a" * 36),
                        # the identifier-labelled shape: `_TOKEN=` is read by the rule
                        # that keys on the noun, so the value is masked either way
                        ("kv-secret", 'SLACK_BOT_TOKEN="' + 'wq2400-bound-value"')):
        assert label in masking.mask_text(live)[1], live


WQ2410_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2410")
# The branch the dispatcher cut for wq#2410: `…-mask-batch-…` carries `sk-` one
# character into a word, the wq#2400 boundary again, and nothing else explains it —
# a branch name is in no committed file.
WQ2410_BRANCH = ("c49a21526822", 16)
# test_token_injection.py's dummy, reached through `git log`'s rendering of the
# revision that still held it unsplit. The two tables cut the same text differently:
# the legacy prefix is greedy enough to eat three characters of the body.
WQ2410_OAT_LEGACY = ("88baf6a624d7", 30)
WQ2410_OAT_TODAY = ("776834423b81", 33)
WQ2410_OAT_IN = "harness/tests/test_token_injection.py:22"


@pytest.mark.skipif(not os.path.isdir(WQ2410_RUN),
                    reason="wq#2410's run dir is not on this host")
def test_wq2410_transcript_burned_nothing():
    """wq#2414's verdict, pinned: 20 values, no credential, no rotation.

    Seventeen are the ISSUE-LOG snapshot paging on its own rows — this backlog writes
    down the shapes it triages, so the log is the densest credential-shaped file in the
    tree — and one is a `key =` assignment read out of mask_triage.py. The two that are
    about this run are the ones asserted below.

    A failure here means the branch name has appeared in a committed file (which would
    move the pin rather than break the verdict), or the dummy that test_token_injection
    holds has moved off line 22.
    """
    records = mask_replay.replay(2410)
    assert len(records) == 20
    assert mask_replay.unaccounted(records) == []
    assert all(r.get("faithful", True) for r in records), "no window was re-cut"
    loose = [r for r in records if r["accounted_in"] is None]
    assert [(r["value_sha256_12"], r["value_len"]) for r in loose] == [WQ2410_BRANCH]
    assert loose[0]["first_seen_in"] == "run:attempt-1.json"
    assert not loose[0]["still_matches_today"], "the boundary fix retired this shape"
    oat = [r for r in records if r["value_sha256_12"] == WQ2410_OAT_LEGACY[0]]
    assert [r["value_len"] for r in oat] == [WQ2410_OAT_LEGACY[1]]
    assert oat[0]["accounted_in"] == WQ2410_OAT_IN


@pytest.mark.skipif(not os.path.isdir(WQ2410_RUN),
                    reason="wq#2410's run dir is not on this host")
def test_the_live_rules_would_not_have_filed_wq2414():
    """Under today's table one value in this run even matches, and it is committed text.

    The leak path that is NOT closed, named here so the next replay does not rediscover
    it: the runtime screen tests the WHOLE match for verbatim presence in the tracked
    tree (burn_filter.py:98 hands `m.group(0)` to is_committed_literal), while the house
    mitigation for a fixture that arms the tripwire is to split its literal at exactly
    the prefix seam — `"sk-ant-" + "oat01…"`, test_token_injection.py:16-25. The two
    defeat each other: a fixture defended that way is invisible to the screen meant to
    excuse it the moment a `git log`/`git show` renders the revision that still held it
    whole, which is how wq#2410 alarmed on a dummy and filed wq#2414. mask_replay
    accounts the body rather than the match, which is why the offline answer here and
    the runtime one differ. Widening the runtime screen is its own ticket with its own
    control — a wider tree is a wider excuse (ops/wq2359-rotation-triage/README.md:68).

    A failure here means the boundary regressed, or the rules stopped firing at all.
    """
    records = mask_replay.replay(2410, patterns=mask_replay.CURRENT_PATTERNS)
    assert mask_replay.unaccounted(records) == []
    assert [(r["value_sha256_12"], r["value_len"]) for r in records] == [WQ2410_OAT_TODAY]
    assert records[0]["accounted_in"] == WQ2410_OAT_IN
    assert WQ2410_BRANCH[0] not in {r["value_sha256_12"] for r in records}
    # Bodies unique to this file, per the note above wq#2400's pin: a string that also
    # occurs in a run's corpus would make this file that run's first explanation.
    for benign in ("branch feat/wq2414-mask-batch-triage-rotation",
                   "dir /home/lumen/ops/wq2414-mask-batch-tooling"):
        assert masking.mask_text(benign)[1] == [], benign
    assert "sk-key" in masking.mask_text("sk-ant-" + "api03-wq2414-bound-value")[1]


def test_legacy_table_is_not_the_live_one():
    """A future tightening of masking.PATTERNS must not silently rewrite history."""
    assert mask_replay.LEGACY_PATTERNS is not mask_replay.CURRENT_PATTERNS
    assert [p[0] for p in mask_replay.LEGACY_PATTERNS] != [
        p[0] for p in mask_replay.CURRENT_PATTERNS] or any(
        legacy[1].pattern != current[1].pattern
        for legacy, current in zip(mask_replay.LEGACY_PATTERNS,
                                   mask_replay.CURRENT_PATTERNS))
    assert masking.MASK == "MASKED"
