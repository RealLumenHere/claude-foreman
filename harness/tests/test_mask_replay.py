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
import shutil
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


def test_a_fixture_printed_cut_short_is_accounted_by_its_prefix():
    """wq#2648: wq#2632's verify.py printed `text[:24] + "..."` of a committed kv
    fixture. `.` is in the kv body charset, so the value was `<fixture prefix>...`,
    which no file holds, and the replay filed a rotate-today row for a test string."""
    tree = {"tests/t.py": FIXTURE}
    printed = 'PASS fires+masks GITEA_TOKEN="' + 'hunter2-hunte...\n'
    records = mask_replay.replay(0, tracked=tree, items={"transcript:a": printed})
    assert [r["accounted_in"] for r in records] == ["tests/t.py:1"]
    assert mask_replay.unaccounted(records) == []


def test_the_truncation_retry_still_burns_a_cut_live_token():
    """The cut prefix of a live token is in no file either, and a stub under the floor
    is excused by nothing."""
    tree = {"tests/t.py": FIXTURE + "abcdefghijk\n"}
    assert mask_replay.account("Zq7xW2pL9mK4nR8...", tree) is None
    assert mask_replay.account("abcdefghijk...", tree) is None


def test_a_preview_cut_under_the_floor_is_accounted_with_its_prefix():
    """wq#2663: wq#2630's run printed `value[:14] + "..."` of a 22-char cfut_ fixture,
    a 9-char body under the floor, and paged it as burned (sha256 740e963a761d)."""
    fixture = "cfut_" + "wq2663" + "preview" + "fixture"
    tree = {"tests/t.py": "X = '" + fixture + "'\n"}
    printed = "  cloudflare   len=22  '" + fixture[:14] + "...'\n"
    records = mask_replay.replay(0, tracked=tree, items={"transcript:a": printed})
    assert [r["accounted_in"] for r in records] == ["tests/t.py:1"]
    assert mask_replay.unaccounted(records) == []
    # the prefix is what makes the stub specific: without it, or with a live head, None
    assert mask_replay.account(fixture[5:14] + "...", tree) is None
    assert mask_replay.account("Zq7xW2pL9...", tree, prefix="cfut_") is None


def test_a_short_body_is_only_accounted_with_its_prefix_in_front():
    """wq#2740: the direct path searched the body ALONE, so the cloudflare row
    sha256:a745825001bc — value `-prefixed`, nine chars of ordinary English — accounted to
    24 tracked files that merely say `vendor-` + that word, and wq#2733 closed the row "no
    rotation, committed text" on one of them (a file holding no `cfut` at all). The same
    coincidence clears a genuinely leaked prefix+body pair, which is why
    burn_filter._is_accounted already demanded the whole match for a body under
    MIN_SECRET_LEN (burn_filter.py:216)."""
    prefix, body = "cfut_", "-prefixed"
    prose = {"doc.md": "none of the vendor" + body + " patterns fire here\n"}
    assert mask_replay.account(body, prose, prefix) is None
    assert mask_replay.account(body, prose) == "doc.md:1"  # no prefix: value is the match
    records = mask_replay.replay(0, tracked=prose,
                                 items={"transcript:a": "a " + prefix + body + " value\n"})
    assert [r["value_sha256_12"] for r in records] == ["a745825001bc"]
    assert [r["accounted_in"] for r in records] == [None]
    assert mask_replay.unaccounted(records) == records
    leaked = {"doc.md": "X = '" + prefix + body + "'\n"}
    assert mask_replay.account(body, leaked, prefix) == "doc.md:1"


def test_a_body_at_the_floor_still_accounts_without_its_prefix():
    """The prefix requirement has to stop at MIN_SECRET_LEN: the house mitigation splits a
    fixture at the prefix seam and leaves only the body committed (ISSUE-LOG rows 814/822,
    where the label wrapped onto the line above its value), so a body long enough to be a
    credible secret must keep excusing itself with no prefix anywhere near it."""
    body = "abcdefghijkl"
    assert len(body) == mask_replay.TRUNCATED_FLOOR
    tree = {"reports/r.md": "TOKEN: " + body + "\n"}
    assert mask_replay.account(body, tree, prefix="TOKEN: ") == "reports/r.md:1"
    wrapped = {"reports/r.md": "TOKEN:\n" + body + "\n"}
    assert mask_replay.account(body, wrapped, prefix="TOKEN: ") == "reports/r.md:2"


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


def write_dated_run(tmp_path, ticket, session, start_ts):
    """`write_run`'s fixture, but with a real `start` event carrying `start_ts` — the
    wq#2715 case, a run whose own event stream can date it."""
    run = write_run(tmp_path, ticket, session)
    events = tmp_path / "events"
    (events / f"fworker-wq{ticket}-a1.jsonl").write_text(
        json.dumps({"ts": start_ts, "event": "start", "session_id": session}) + "\n"
        + OWN_OUTPUT)
    return run


def test_auto_rules_dates_a_same_day_ticket_to_the_current_table(tmp_path, monkeypatch,
                                                                  capsys):
    """wq#2715 (ISSUE-LOG row 793, wq#2696): the default used to be the retired legacy
    table regardless of the run's own date, so a same-day ticket silently misreplayed —
    surfacing noise the ticket never fired on and omitting the values it exists to
    check. CALLED_IDENT only fires as kv-secret under LEGACY_PATTERNS (see
    test_legacy_rules_fire_on_a_called_identifier_and_current_rules_do_not); a run that
    started well after the legacy table's 2026-09-20T22:35 retirement must be scored
    under CURRENT_PATTERNS with NO --rules flag, so that snapshot must produce nothing."""
    write_dated_run(tmp_path, 4343, "sess-1", "2026-09-25T13:30:03Z")
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setattr(mask_replay, "tracked_text", lambda *a, **k: {})
    capsys.readouterr()
    mask_replay.main(["4343", "--json"])
    out, err = capsys.readouterr()
    records = json.loads(out)
    assert not any(r["first_seen_in"].startswith("snapshot:") for r in records)
    assert "--rules auto -> current" in err


def test_auto_rules_keeps_legacy_for_a_run_before_the_cutoff(tmp_path, monkeypatch,
                                                              capsys):
    """The other side of the same boundary: a run dated before the retirement must
    still replay under the table that was actually live when it fired."""
    write_dated_run(tmp_path, 4344, "sess-1", "2026-09-19T00:00:00Z")
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setattr(mask_replay, "tracked_text", lambda *a, **k: {})
    capsys.readouterr()
    mask_replay.main(["4344", "--json"])
    out, err = capsys.readouterr()
    records = json.loads(out)
    assert any(r["first_seen_in"].startswith("snapshot:") and r["label"] == "kv-secret"
              for r in records)
    assert "--rules auto -> legacy" in err


def test_forcing_legacy_on_a_same_day_run_warns(tmp_path, monkeypatch, capsys):
    """Forcing `--rules legacy` is still allowed (replaying what actually fired an old
    ticket needs it), but doing so on a run dated at/after the retirement must say so
    loudly — this is the silent-misreplay shape wq#2696 found, now an explicit warning
    instead of a clean report that never examined the ticket's own defining values."""
    write_dated_run(tmp_path, 4345, "sess-1", "2026-09-25T13:30:03Z")
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setattr(mask_replay, "tracked_text", lambda *a, **k: {})
    capsys.readouterr()
    mask_replay.main(["4345", "--rules", "legacy", "--json"])
    out, err = capsys.readouterr()
    records = json.loads(out)
    assert any(r["first_seen_in"].startswith("snapshot:") and r["label"] == "kv-secret"
              for r in records)
    assert "WARNING --rules legacy forced" in err


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


# --- wq#2677: what the rule declined to alarm on -----------------------------

SUPPRESSED_CALL = "        token = read_" + "oauth_token()\n"


def test_the_runs_own_suppression_rows_are_read_back(tmp_path, monkeypatch):
    """The run's mask-hits file is the authority: those rows were written by the
    process that read the stream, so they say what the table it was masking with
    actually suppressed — and they exist on a run that never alarmed, which is the
    case the whole record exists for."""
    run = write_run(tmp_path, 4242, "sess-1")
    (run / "mask-hits-1.json").write_text(json.dumps([
        {"label": "kv-secret", "value_len": 23, "value_sha256_12": "abc123abc123",
         "prefix": 'TOKEN="', "context": "", "ts": "2026-09-25T00:00:00Z"},
        {"label": "kv-secret", "suppressed": "call", "value_len": 16,
         "value_sha256_12": "166e2282ed20", "prefix": "token = ",
         "ts": "2026-09-25T00:00:01Z"},
    ]))
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(run.parent))
    rows = mask_replay.suppressed_records(4242)
    # the hit row in the same file is not a suppression and must not be reported as one
    assert [(r["suppressed"], r["value_sha256_12"], r["derived"]) for r in rows] == [
        ("call", "166e2282ed20", False)]
    assert rows[0]["source"] == "mask-hits-1.json"
    out = io.StringIO()
    mask_replay.report_suppressed(4242, rows, out=out)
    assert "recorded by the run" in out.getvalue()
    assert "no rotation" in out.getvalue()


def test_a_run_that_recorded_nothing_falls_back_to_deriving_from_the_corpus(tmp_path,
                                                                            monkeypatch):
    """A run older than wq#2677 wrote no suppression rows at all. Today's table over
    the run's corpus is the only answer available, and it is marked `derived` because
    a carve-out added since could not have been what went silent then."""
    run = write_run(tmp_path, 4242, "sess-1")
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(run.parent))
    rows = mask_replay.suppressed_records(4242, items={"snapshot:a": SUPPRESSED_CALL,
                                                       "snapshot:b": SUPPRESSED_CALL})
    # one row for the value however many artefacts carried it
    assert [(r["suppressed"], r["derived"]) for r in rows] == [("call", True)]
    assert "derived from today's table" in _report_text(4242, rows)
    # and it stays out of the verdict: today's table scores the same corpus at zero,
    # which is the silence these rows exist to explain (the legacy table above still
    # fires on a called identifier, which is why it is the one the backlog is scored
    # under).
    assert mask_replay.replay(4242, patterns=mask_replay.CURRENT_PATTERNS, tracked={},
                              items={"snapshot:a": SUPPRESSED_CALL}) == []


def _report_text(ticket, rows):
    out = io.StringIO()
    mask_replay.report_suppressed(ticket, rows, out=out)
    return out.getvalue()


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


def test_a_changed_file_resolves_from_the_runs_own_snapshot_before_git_history(
        tmp_path, monkeypatch):
    """wq#2370: the run's own frozen pre-image is stronger evidence than a same-sized
    git revision, and it is the only source available at all when the read happened
    outside any git history (a scratch path, or an edit that never landed). A
    byte-exact fp match against the snapshot names the run's own bytes, not merely a
    same-sized candidate — so it is tried, and wins, before blob_at_size."""
    run = write_run(tmp_path, 4242, "sess-1")
    read = tmp_path / "scratch.txt"
    read.write_text(FIXTURE)
    fp = mask_replay._stat_fp(read)
    snap_name = f"20260920T000005Z-{mask_replay._sanitize_name(str(read))}"
    shutil.copy2(read, run / "snapshots" / snap_name)
    read.write_text(CALLED_IDENT)  # the file has since changed underneath the replay
    (tmp_path / "events" / "read_cache" / "sess-1.jsonl").write_text(
        json.dumps({"path": str(read), "offset": "1", "limit": "1", "fp": fp}) + "\n")
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(run.parent))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    items = mask_replay.corpus(4242)
    key = next(k for k in items if k.startswith("read:") and "scratch.txt" in k)
    assert key.endswith(f"#snapshot:{snap_name}")
    assert items[key] == FIXTURE


def test_a_value_committed_in_another_repo_the_run_read_is_accounted(tmp_path,
                                                                     monkeypatch, capsys):
    """wq#2557: wq#2555 edited minerva through a linked worktree, and a comment
    committed on minerva's main paged as BURNED? because only this repo's tree was
    searched. The primary checkout's HEAD accounts it; a value that exists only on
    the worker's own branch stays unaccounted."""
    repo = tmp_path / "minerva"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "src.py").write_text("filler\n" + FIXTURE)
    git(repo, "add", "src.py")
    git(repo, "commit", "-qm", "landed")
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "wip", str(wt))
    src = wt / "src.py"
    src.write_text("filler\n" + FIXTURE + LIVE)
    git(wt, "commit", "-qam", "worker's own edit")

    run = write_run(tmp_path, 4242, "sess-1")
    (tmp_path / "events" / "read_cache" / "sess-1.jsonl").write_text(
        read_record(src, 1, 3, src.stat().st_size))
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(run.parent))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setattr(mask_replay, "tracked_text", lambda *a, **k: {})
    items = mask_replay.corpus(4242)
    common = mask_replay.foreign_repos(items)
    assert [os.path.realpath(c) for c in common] == [os.path.realpath(repo / ".git")]

    records = mask_replay.replay(4242, tracked=mask_replay.committed_text(common[0]),
                                 items=items)
    by_len = {r["value_len"]: r for r in records if r["label"] == "kv-secret"}
    assert by_len[23]["accounted_in"].endswith("minerva/src.py:2")
    assert [r["value_len"] for r in mask_replay.unaccounted(records)] == [32]

    capsys.readouterr()
    assert mask_replay.main(["4242", "--json"]) == 1
    cli = json.loads(capsys.readouterr().out)
    assert [r["value_len"] for r in mask_replay.unaccounted(cli)] == [32]


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
    _assert_kv_hits_are_test_fixtures(2288)


def _tests_only(tracked=None):
    """The committed tree narrowed to test modules, for a pin whose claim is "a test
    fixture explains this" rather than "something in the tree does".

    wq#2765: `account` returns the FIRST tracked file holding a value, in `git
    ls-files` order, so a landed report that quotes a dummy (6778336 put
    2667_verify_wq2630_judgment_wq2290_closure.md at the repo ROOT, which sorts ahead
    of harness/) wins the lookup and `"tests/test_" in accounted_in` starts scoring a
    race. Scored against the test files alone the verdict is the same and stronger:
    the fixture explains the value whatever else in the tree also does.
    """
    tracked = mask_replay.tracked_text() if tracked is None else tracked
    return {path: text for path, text in tracked.items() if "tests/test_" in path}


def _assert_kv_hits_are_test_fixtures(ticket_id):
    records = mask_replay.replay(ticket_id, patterns=mask_replay.CURRENT_PATTERNS,
                                 tracked=_tests_only())
    kv = [r for r in records if r["label"] == "kv-secret"]
    assert kv, "the fixture text should still reproduce the kv-secret hit"
    for r in kv:
        assert r["accounted_in"] and "tests/test_" in r["accounted_in"], r


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

# The gate snapshot that holds all three — the pre-image of the edit the worker was
# making, frozen 22:51:23, one second before the alarm filed wq#2337. Holding them is
# not carrying them into context: wq#2393 puts all three on line 657, one line above
# the read window and two above the edit hunk, so nothing here ever reached the model.
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
    assert [s.rsplit("@", 1)[1].split("#", 1)[0] for s in reads] == ["658"]
    # wq#2370: ISSUE-LOG.md changes constantly and this run left no frozen
    # pre-image of it, so the window is neither the run's own snapshot nor any
    # git revision at its recorded size — labelled unresolved, not silently live.
    assert [s.rsplit("#", 1)[1] for s in reads] == ["unresolved"]


@pytest.mark.skipif(not os.path.isdir(WQ2253_RUN),
                    reason="wq#2253's run dir is not on this host")
def test_wq2253_alarmed_on_the_line_below_every_window_the_model_got():
    """wq#2393, the question the test above leaves open: a snapshot is a whole file,
    so "only in the snapshot" still allows that some stream line carried the same
    bytes. This closes it by locating the values inside the file.

    All three are on ONE line, 657, and the run touched the doctrine log twice: a Read
    that returned lines 658 onward and an Edit that patched from 659 (the hunk in the
    transcript's own record of it). The alarm fired on the line immediately above both
    windows — off by one from anything the model was handed. Nothing was burned.

    The failing alternative was a stream line the transcript does not persist, and the
    channel is real: run_child merges the child's stderr into the same pipe it reads
    frames from, and a frame's envelope carries CLI bookkeeping beside the message. So
    the screen now scores the message, not the line (fworker.screen_stream_hits), and
    this row is the case that fix has to suppress.
    """
    with open(os.path.join(WQ2253_RUN, "snapshots",
                           WQ2253_DOCTRINE_SNAPSHOT.split(":", 1)[1]),
              errors="replace") as fh:
        lines = fh.read().splitlines()
    rule = dict(mask_replay.LEGACY_PATTERNS)["sk-key"]
    assert {n for n, text in enumerate(lines, 1) if rule.search(text)} == {657}
    records = mask_replay.replay(2253, items={WQ2253_DOCTRINE_SNAPSHOT: lines[656]})
    assert {r["value_sha256_12"] for r in records} == set(WQ2253_SK_KEY)


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
    _assert_kv_hits_are_test_fixtures(2300)


# The one `kv-secret` value wq#1819 attempt 8 put in front of its model. Unlike every
# other pin in this file the identity is not asserted as a bare hash constant: the
# value is a PUBLIC PYTHON IDENTIFIER, so the test can name it outright and hash it
# back, which is a strictly stronger pin. A bare constant only says "unchanged since
# someone looked"; this says what the string IS, and fails loudly if a future rule ever
# re-reads a different string into this slot.
WQ1819_KV_IDENTIFIER = b"read_oauth_token"

# wq#2678: the corpus these two tests replay, frozen into the tree.
#
# `corpus(1819)` reads a LIVE run dir, and a live input cannot pin a historical
# verdict. On 2026-09-25 an unrelated lane (wq1819-a10) had its newer `fworker.py`
# frozen into `_runs/wq1819/snapshots/`, and that one snapshot of a file nobody here
# was reasoning about took the record count from 1 to 3 and moved this identifier's
# `accounted_in` off `fworker.py` onto a judgment doc that merely quotes it. Neither
# movement says anything about masking; both would turn this test red, or quietly
# green for the wrong reason, on a day no rule changed. That is the anti-pattern
# wq#2630 names, and the pin is the cure the ticket asks for.
#
# The fixture is not an excerpt somebody chose: it is the exact 200-line window
# attempt 8's `Read` put in front of `nephthys-qwen14b`, keyed in the live corpus as
# WQ1819_WINDOW and recorded in that session's read cache as
# `offset=1300 limit=200 fp=200188:...`, re-cut from git history at the recorded size.
# Not hand-trimmed around the hit on purpose — a window cut down to the one line the
# rule fires on could not fail, where these 200 lines can: widen the kv rule and the
# other 199 get their say.
WQ1819_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "fixtures", "wq1819-attempt8-fworker-window.txt")
WQ1819_FIXTURE_REL = "harness/tests/fixtures/wq1819-attempt8-fworker-window.txt"
WQ1819_FIXTURE_SHA = "e91b65845d1dd341e1cc0a2a3bbeb0c0ae7bf9ab51cf4ada3367e85fdd7e61ee"
WQ1819_WINDOW = "read:/home/lumen/foreman/harness/fworker.py@1300#git-history"


def wq1819_corpus():
    """attempt 8's window, under its live-corpus key, checked against its hash.

    The hash is the half that makes this a pin rather than a file: `replay` would
    happily score an edited fixture, and a fixture anyone can edit re-opens exactly
    the hole the live run dir had."""
    with io.open(WQ1819_FIXTURE, encoding="utf-8") as fh:
        text = fh.read()
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == WQ1819_FIXTURE_SHA, \
        f"{WQ1819_FIXTURE_REL} was edited; the wq#1819 verdict is no longer pinned"
    return {WQ1819_WINDOW: text}


def wq1819_tracked():
    """The committed tree MINUS the fixture above.

    Committing the window makes the tree hold a second copy of `read_oauth_token`, and
    `account()` names the first tracked file holding a value — so left in, the fixture
    would account for its own contents and `unaccounted() == []` would be a tautology
    over a file this test wrote. Dropping it keeps that assertion a claim about the
    real tree."""
    tracked = mask_replay.tracked_text()
    tracked.pop(WQ1819_FIXTURE_REL, None)
    return tracked


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
    which is not a mitigation but is worth recording next to the verdict.

    wq#2673 dropped `len(records) == 1` because `account()` names the FIRST tracked file
    holding a value (mask_replay.py:379), so the count drifted with whatever else landed
    in the corpus. wq#2678 puts the count back and fixes the drift at its source: the
    input is now the pinned window, not a live run dir (see WQ1819_FIXTURE), so one hit
    is a fact about attempt 8's context rather than about what got snapshotted today.
    `unaccounted(records) == []` is the verdict proper and is still scored against the
    live tree — that one SHOULD move if the identifier ever stops being public source."""
    records = mask_replay.replay(1819, items=wq1819_corpus(),
                                 tracked=wq1819_tracked())
    assert mask_replay.unaccounted(records) == []
    assert len(records) == 1
    kv = records[0]
    assert kv["label"] == "kv-secret"
    assert kv["value_sha256_12"] == hashlib.sha256(WQ1819_KV_IDENTIFIER).hexdigest()[:12]
    assert kv["value_len"] == len(WQ1819_KV_IDENTIFIER) == 16
    # a call, not a value: the recorded line keeps the `()` the rule failed to look at
    assert kv["line"].endswith("()")
    # committed source, hence public to every worker, hence not a secret — wherever a
    # tracked file happens to hold it; the path itself is not pinned (see docstring)
    assert kv["accounted_in"]


def test_the_live_rules_would_not_have_filed_wq2347():
    """The leak path, closed, and closed twice. kv-secret's tail lookahead (wq#2288)
    ends a match on `(?![A-Za-z0-9._-]|\\()`, so a call can no longer be read as a
    value and the whole corpus now scores zero. Were it ever to match again, the second
    screen holds independently: `burn_filter` pages only on a value it cannot find in a
    committed file, and this one is in two.

    Zero is per-corpus rather than per-label, so this also forecloses the sibling
    tickets that would otherwise be filed against this run under the other labels.

    wq#2678: same pinned window as the test above, and for the same reason — `[0]` over
    a live corpus was reading whichever record a snapshot happened to sort first, so the
    `still_matches_today` half was asserting about an arbitrary row. Pinned, there is
    one row and it is the one the docstring is about; the claim is now over all of
    them."""
    items, tracked = wq1819_corpus(), wq1819_tracked()
    assert mask_replay.replay(1819, patterns=mask_replay.CURRENT_PATTERNS,
                              items=items, tracked=tracked) == []
    records = mask_replay.replay(1819, items=items, tracked=tracked)
    assert records
    assert all(r["still_matches_today"] is False for r in records)


def test_the_wq1819_pin_refuses_an_edited_window(tmp_path, monkeypatch):
    """The guard in `wq1819_corpus` has to be able to fail, or the two tests above are
    pinned to a file rather than to bytes.

    Appending one more credential-shaped line is the exact drift wq#2678 was filed for —
    a snapshot of a newer `fworker.py` adding called identifiers nobody reviewed — so
    that is what the probe appends. The window must be rejected before `replay` ever
    scores it; a fixture that scores first and mismatches later is just a slower live
    corpus."""
    with io.open(WQ1819_FIXTURE, encoding="utf-8") as fh:
        edited = fh.read() + '\n        key = read_litellm_key()\n'
    stray = tmp_path / "wq1819-window.txt"
    stray.write_text(edited, encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "WQ1819_FIXTURE", str(stray))
    with pytest.raises(AssertionError, match="no longer pinned"):
        wq1819_corpus()
    # and the appended line is a real extra hit, so the rejection is not vacuous —
    # scored directly, the edited window is the 2-record corpus the guard refuses
    assert len(mask_replay.replay(1819, items={WQ1819_WINDOW: edited},
                                  tracked=wq1819_tracked())) == 2


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
    # wq#2765 (ISSUE-LOG row 787, root cause): the branch's MINTED/STATIC gateway-key
    # dummies (test_gateway_key_per_ticket.py:23-24, whole on the branch, landed on
    # main renamed `-dummy` in ef8f568), cut by the LEGACY sk-key rule at `sk-<letters
    # and dashes>` so what it reports is the 16-char tail of each. wq#2423 (34d1529)
    # stopped excusing them as a retired rule — today's table recuts the same span
    # into a value main does not hold either — so they surfaced as loose, and the
    # only committed copy is the branch's, which is what this map is for.
    "3a455b3d761c": "harness/tests/test_gateway_key_per_ticket.py",
    "27ca921139e8": "harness/tests/test_gateway_key_per_ticket.py",
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
    pages only on a value it cannot find in a committed file. wq#2806 added a second:
    the daemon suite's `TAPO_PASS=` fixture, which the `_PASS` noun now reads and which
    is likewise committed (token-engine/tests/test_daemon.py)."""
    items = mask_replay.corpus(2278)
    records = mask_replay.replay(2278, patterns=mask_replay.CURRENT_PATTERNS,
                                 items={WQ2348_TRANSCRIPT: items[WQ2348_TRANSCRIPT]})
    live = [r for r in records if r["label"] in WQ2348_LABELS]
    assert {r["value_sha256_12"] for r in live} == {"ae71c4cdf5d7", "7b58e14cabd1"}
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


def _tree_only():
    """The committed repo, with the shared doctrine log left out of the tracked view.

    wq#2418, closing ISSUE-LOG row 630: a pin that asserts "this value is in no
    committed file" cannot be scored against kb/ISSUE-LOG.md, because the row that
    settles a rotation quotes the value it settled and accounts it on the next run —
    row 629 did exactly that to WQ2410_BRANCH and turned the pin below red. The log
    stays in the CLI's default view (a value the log explains is still explained); it
    is only these pins, whose subject is an unaccounted value, that key off the tree.
    """
    return mask_replay.tracked_text(extra=[])


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

    A failure here means the branch name has appeared in a committed FILE (which would
    move the pin rather than break the verdict), or the dummy that test_token_injection
    holds has moved off line 22. An ISSUE-LOG row quoting the branch body no longer
    counts as that file — see _tree_only.

    wq#2419 re-verified this pin against ISSUE-LOG row 629's own quote of
    WQ2410_BRANCH (kb/ISSUE-LOG.md:724): _tree_only excludes the log, so the quote
    does not re-account the value and the assertion below still holds.
    """
    records = mask_replay.replay(2410, tracked=_tree_only())
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
    records = mask_replay.replay(2410, patterns=mask_replay.CURRENT_PATTERNS,
                                 tracked=_tree_only())
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


WQ2417_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2417")
# Both 16-char bodies are branch names, brute-forced at offset 39 of
# `feat/wq2417-verify-wq-2415-mask-provenance-screen-grep-a1` and of the wq#2415
# branch that run was verifying. `mask-` puts `sk-` one character into a word, so
# every branch in this chain is a standing sk-key hit and none is in a committed file.
WQ2417_BRANCH_RUN = ("1c3542d49e6b", 16)
WQ2417_BRANCH_TX = ("148ab9072eee", 16)
# wq#2417's own inline probe literal: `burn_filter.Hit('sk-ant-oat…', 'oat…')` typed
# into a `python3 -c` that round-trips a Hit through copy/deepcopy/pickle. The two
# tables cut the same 29 bytes at different seams, and that difference is wq#2418.
WQ2417_OAT_LEGACY = ("c954aebcbad7", 19)
WQ2417_OAT_TODAY = ("c0f411fd3188", 22)
WQ2417_OAT_LITERAL_LEN = 29


def _with_full_literal(ticket, patterns):
    """Replay, adding sha256/len of the WHOLE match to each record.

    Rides replay's in-process verdict seam, so the value is hashed where it is read
    and never reaches a record, a PR body or this file.
    """
    def verdict(label, prefix, value):
        whole = prefix + value
        return {"full_sha12": hashlib.sha256(
                    whole.encode("utf-8", "replace")).hexdigest()[:12],
                "full_len": len(whole)}
    return mask_replay.replay(ticket, patterns=patterns, tracked=_tree_only(),
                              verdict=verdict)


@pytest.mark.skipif(not os.path.isdir(WQ2417_RUN),
                    reason="wq#2417's run dir is not on this host")
def test_wq2417_transcript_burned_nothing():
    """wq#2418's verdict under the table the ticket names: 20 values, 1 unaccounted.

    Seventeen are the ISSUE-LOG snapshot paging on its own rows again. Of the three
    that are about this run, two are branch names; the third is the oat-prefixed
    literal wq#2423 is about — today's table still fires on that line (it just cuts a
    shorter span), so it is no longer waved through as "rule-fixed" (wq#2423). It was
    excused as a closed rule before that fix; it pages now, which is the bug wq#2418
    found and this ticket's `today_closed` field corrects.
    """
    records = mask_replay.replay(2417, tracked=_tree_only())
    assert len(records) == 20
    assert [r["value_sha256_12"] for r in mask_replay.unaccounted(records)] == [
        WQ2417_OAT_LEGACY[0]]
    assert all(r.get("faithful", True) for r in records), "no window was re-cut"
    loose = [r for r in records if r["accounted_in"] is None]
    assert [(r["value_sha256_12"], r["value_len"]) for r in loose] == [
        WQ2417_BRANCH_RUN, WQ2417_OAT_LEGACY, WQ2417_BRANCH_TX]
    assert loose[0]["first_seen_in"] == "run:attempt-1.json"
    assert not any(r["still_matches_today"] for r in loose)


@pytest.mark.skipif(not os.path.isdir(WQ2417_RUN),
                    reason="wq#2417's run dir is not on this host")
def test_wq2418_alarm_value_is_one_literal_two_tables_cut_apart():
    """The value wq#2418 is about survives today's table, and is the same 29 bytes.

    `mask_replay.py 2417` (legacy table) answers 0 unaccounted; `mask_triage.py 2418
    --source 2417`, which picks rules by the alarm's created_at, answers 1 UNACCOUNTED.
    They do not disagree about any byte — the legacy prefix `[a-zA-Z-]*` is greedy
    enough to eat `oat` off the front of the body, so the same match yields a 19-char
    value there and a 22-char one here. Equal full-literal hashes are the proof.

    29 characters end to end is the fact that settles the rotation: an issued
    `sk-ant-oat01-`/`sk-ant-api03-` key carries a ~95-char body, this carries 16 past
    the rule's `{16,}` floor, and burn_filter's live screen puts it in no environment
    variable of the worker that replayed it.
    """
    legacy = _with_full_literal(2417, mask_replay.LEGACY_PATTERNS)
    today = _with_full_literal(2417, mask_replay.CURRENT_PATTERNS)
    oat = [r for r in legacy if r["value_sha256_12"] == WQ2417_OAT_LEGACY[0]]
    assert [r["value_len"] for r in oat] == [WQ2417_OAT_LEGACY[1]]
    assert [(r["value_sha256_12"], r["value_len"]) for r in today] == [WQ2417_OAT_TODAY]
    assert today[0]["full_sha12"] == oat[0]["full_sha12"], "same bytes, different cut"
    assert today[0]["full_len"] == oat[0]["full_len"] == WQ2417_OAT_LITERAL_LEN
    assert today[0]["first_seen_in"] == oat[0]["first_seen_in"]
    assert today[0]["accounted_in"] is None, "a literal typed at a prompt is in no file"
    assert mask_replay.unaccounted(today) == today


def test_a_recut_prefix_is_excused_as_a_closed_rule():
    """wq#2423: a widened prefix must not launder a live shape as a retired rule.

    `still_matches_today` asks whether today's table reproduces the identical VALUE
    STRING — a widened prefix always fails that, since it recuts the same span into a
    different string. `unaccounted()` no longer keys off that flag directly; it keys
    off `today_closed`, which is true only when today's table does not fire on the
    LINE at all, or when whatever it cuts today is itself accounted for. Here one line
    is credential-shaped under both tables and committed text under neither, so both
    replays must page it — the legacy replay no longer gets to call this "rule-fixed".

    Kept free of any run dir: the defect is in the accounting, not in wq#2417's corpus.
    """
    line = "h=b.Hit('sk-ant-" + "oat01-" + "z" * 16 + "')\n"
    items = {"transcript:synthetic": line}
    legacy = mask_replay.replay(0, tracked={}, items=items)
    today = mask_replay.replay(0, patterns=mask_replay.CURRENT_PATTERNS,
                               tracked={}, items=items)
    assert [(r["prefix"], r["value_len"]) for r in legacy] == [("sk-ant-oat", 19)]
    assert [(r["prefix"], r["value_len"]) for r in today] == [("sk-ant-", 22)]
    assert not legacy[0]["still_matches_today"], "the recut, read as a retired shape"
    assert not legacy[0]["today_closed"], "but the line still fires today, unaccounted"
    assert mask_replay.unaccounted(today) == today, "today's table would page this"
    assert mask_replay.unaccounted(legacy) == legacy, "and now so does the legacy replay"


def test_a_line_today_truly_drops_is_still_excused():
    """Re-planted control (ops/wq2359-rotation-triage/README.md:68-75): the wq#2423 fix
    must still fall through to `rule-fixed` for a shape today's table genuinely drops —
    a fixture pinned independent of any ticket corpus, so it can fail on its own.

    Unlike the recut case above, this line's legacy label has no counterpart at all in
    CURRENT_PATTERNS on the same line: today's table does not fire on it, so it is the
    "does not fire on the LINE at all" branch of the fix, not the recut-and-accounted
    one, and must stay excused.
    """
    line = "TOKEN = read_oauth_token_from_vault()\n"
    items = {"transcript:synthetic": line}
    legacy = mask_replay.replay(0, tracked={}, items=items)
    assert legacy, "fixture must actually fire the legacy table, or this proves nothing"
    today_hits = [r for r in legacy if not r["still_matches_today"]]
    assert today_hits, "fixture must actually exercise a legacy-only shape"
    for rec in today_hits:
        assert rec["today_closed"], "a line today's table never touches stays excused"
    assert mask_replay.unaccounted(today_hits) == []


def test_legacy_table_is_not_the_live_one():
    """A future tightening of masking.PATTERNS must not silently rewrite history."""
    assert mask_replay.LEGACY_PATTERNS is not mask_replay.CURRENT_PATTERNS
    assert [p[0] for p in mask_replay.LEGACY_PATTERNS] != [
        p[0] for p in mask_replay.CURRENT_PATTERNS] or any(
        legacy[1].pattern != current[1].pattern
        for legacy, current in zip(mask_replay.LEGACY_PATTERNS,
                                   mask_replay.CURRENT_PATTERNS))


# --- wq#2543: the rotated-value ledger ----------------------------------------
#
# wq#2514 AC1 (mask_replay exits 0) is unreachable for a real burn: rotating the
# value never removes it from the run's frozen corpus, so an unrotated replay would
# report it forever. These pin the settlement — a ledger entry keyed by the same
# hash the report already carries, holding no value, that turns a rotated hit from
# BURNED? into ROTATED and drops it out of `unaccounted()`.

SHA_A = hashlib.sha256(b"Zq7xW2pL9mK4nR8tY6vB3cF5hJ1dS0aG").hexdigest()[:12]


def test_load_rotated_ledger_returns_empty_for_a_missing_file(tmp_path):
    assert mask_replay.load_rotated_ledger(str(tmp_path / "nope.json")) == {}


def test_load_rotated_ledger_returns_empty_for_unparseable_json(tmp_path):
    bad = tmp_path / "ledger.json"
    bad.write_text("not json")
    assert mask_replay.load_rotated_ledger(str(bad)) == {}


def test_record_rotated_writes_a_seedable_entry(tmp_path):
    path = tmp_path / "rotated_ledger.json"
    record = mask_replay.record_rotated(
        "6901490c4d03", "wq2514",
        note="Gitea token id9 (mailbot-bootstrap-20260902), rotated+deleted by "
             "orch-b2-creds",
        path=str(path), rotated_at="2026-09-24T00:00:00Z")
    assert record["value_sha256_12"] == "6901490c4d03"
    on_disk = json.loads(path.read_text())
    assert on_disk == [record]
    # only a hash, a timestamp, a ticket and a note ever reach the ledger
    assert set(record) == {"value_sha256_12", "rotated_at", "ticket", "note"}


def test_record_rotated_dedupes_on_the_same_hash(tmp_path):
    path = tmp_path / "rotated_ledger.json"
    mask_replay.record_rotated("abc123abc123", "wq1", path=str(path),
                               rotated_at="2026-09-01T00:00:00Z")
    second = mask_replay.record_rotated("abc123abc123", "wq2", path=str(path),
                                        rotated_at="2026-09-02T00:00:00Z")
    on_disk = json.loads(path.read_text())
    assert on_disk == [second]
    assert on_disk[0]["ticket"] == "wq2"


def test_load_rotated_ledger_round_trips_record_rotated(tmp_path):
    path = tmp_path / "rotated_ledger.json"
    mask_replay.record_rotated("6901490c4d03", "wq2514", path=str(path),
                               rotated_at="2026-09-24T00:00:00Z")
    ledger = mask_replay.load_rotated_ledger(str(path))
    assert ledger["6901490c4d03"]["ticket"] == "wq2514"


def test_a_ledgered_value_is_rotated_not_unaccounted():
    ledger = {SHA_A: {"value_sha256_12": SHA_A, "rotated_at": "2026-09-24T00:00:00Z",
                       "ticket": "wq2514"}}
    records = mask_replay.replay(0, tracked={}, items={"snapshot:a": LIVE},
                                 rotated=ledger)
    assert records[0]["value_sha256_12"] == SHA_A
    assert records[0]["rotated"] == ledger[SHA_A]
    assert mask_replay.unaccounted(records) == []


def test_an_unledgered_value_is_still_unaccounted():
    """No hash in the ledger means no free pass — only wq#2514 asked to make an
    exception, not to disable the check."""
    records = mask_replay.replay(0, tracked={}, items={"snapshot:a": LIVE},
                                 rotated={})
    assert records[0]["rotated"] is None
    assert mask_replay.unaccounted(records) == records


def test_replay_defaults_to_loading_the_on_disk_ledger(tmp_path, monkeypatch):
    path = tmp_path / "rotated_ledger.json"
    mask_replay.record_rotated(SHA_A, "wq2514", path=str(path),
                               rotated_at="2026-09-24T00:00:00Z")
    monkeypatch.setattr(mask_replay, "ROTATED_LEDGER_PATH", str(path))
    records = mask_replay.replay(0, tracked={}, items={"snapshot:a": LIVE})
    assert records[0]["rotated"]["ticket"] == "wq2514"


def test_report_marks_a_rotated_value_rotated_not_burned():
    ledger = {SHA_A: {"value_sha256_12": SHA_A, "rotated_at": "2026-09-24T00:00:00Z",
                       "ticket": "wq2514"}}
    records = mask_replay.replay(0, tracked={}, items={"snapshot:a": LIVE},
                                 rotated=ledger)
    out = io.StringIO()
    exit_code = mask_replay.report(0, records, out=out)
    text = out.getvalue()
    assert exit_code == 0
    assert "[ROTATED]" in text
    assert "BURNED?" not in text
    assert "rotated  : 2026-09-24T00:00:00Z (wq2514)" in text


def test_report_masks_a_prefix_todays_rules_read_as_a_credential():
    """wq#2695: the report printed wq#2677's legacy sk-key prefix, a slice of the
    branch name `mask-alarm-record-a-low-confidence`, raw. Today's sk-key rule reads
    it as a key, so replaying wq#2677 re-fired the tripwire on the replay run."""
    prefix = "sk-" "alarm-record-a-low-con"
    assert masking.mask_text(prefix)[1] == ["sk-key"]
    records = [{"label": "sk-key", "prefix": prefix, "value_len": 16,
                "value_sha256_12": "1619532ef427", "first_seen_in": "snapshot:a",
                "accounted_in": None, "still_matches_today": False,
                "today_closed": True, "rotated": None, "line": ""}]
    out = io.StringIO()
    mask_replay.report(0, records, out=out)
    assert masking.mask_text(out.getvalue())[1] == []
    assert "prefix='sk-" in out.getvalue()


def test_a_settled_burn_makes_the_cli_exit_0(tmp_path, monkeypatch):
    """The AC1 shape wq#2514 filed this ticket over: a real, unaccounted burn that
    stays unaccounted forever without a ledger. Same fixtures as
    test_cli_exit_code_separates_a_burn_from_a_fixture, but this time the loose
    value is a rotated one, and the run must exit 0 rather than page again."""
    write_run(tmp_path, 4242, "sess-1")
    monkeypatch.setattr(mask_replay, "RUNS_ROOT", str(tmp_path / "_runs"))
    monkeypatch.setattr(mask_replay, "EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setattr(mask_replay, "tracked_text", lambda *a, **k: {})
    assert mask_replay.main(["4242"]) == 1

    kv_sha = hashlib.sha256(b"hunter2-hunter2-hunter2").hexdigest()[:12]
    ledger = tmp_path / "rotated_ledger.json"
    mask_replay.record_rotated(kv_sha, "wq4242", path=str(ledger),
                               rotated_at="2026-09-24T00:00:00Z")
    assert mask_replay.main(["4242", "--rotated-ledger", str(ledger)]) == 0


def test_record_rotated_cli_subcommand_never_takes_a_value(tmp_path):
    path = tmp_path / "rotated_ledger.json"
    exit_code = mask_replay.main([
        "record-rotated", "--sha256-12", "6901490c4d03", "--ticket", "wq2514",
        "--note", "Gitea token id9, rotated+deleted by orch-b2-creds",
        "--rotated-ledger", str(path)])
    assert exit_code == 0
    ledger = mask_replay.load_rotated_ledger(str(path))
    assert ledger["6901490c4d03"]["ticket"] == "wq2514"


def test_the_two_unrotated_wq2511_github_values_stay_unaccounted():
    """Frank confirmed (GitHub console) that the two GitHub PATs from wq#2511 —
    27ec5cf7418a and b7fe1ad62725 — are NOT rotated yet. A ledger seeded only with
    the Gitea entry must not excuse them."""
    ledger = {"6901490c4d03": {"value_sha256_12": "6901490c4d03",
                               "rotated_at": "2026-09-24T00:00:00Z",
                               "ticket": "wq2514"}}
    items = {"snapshot:a": PRINTED}
    records = mask_replay.replay(0, tracked={}, items=items, rotated=ledger)
    ghp = next(r for r in records if r["prefix"] == "ghp_")
    assert ghp["value_sha256_12"] != "6901490c4d03"
    assert ghp["rotated"] is None
    assert ghp in mask_replay.unaccounted(records)
    assert masking.MASK == "MASKED"


WQ2650_VALUE = ("55e0c019e958", 33)


def test_wq2650_value_is_the_wq2350_fixture_plus_a_regex_dot():
    """wq#2650's rotation row, settled: no credential, nothing rotated.

    wq#2638 attempt 1 grepped the ISSUE-LOG for wq#2350's concatenation fixture with a
    regex `.` glued on (`grep -o -n ".\\{60\\}<fixture>.\\{40\\}"`). The cloudflare body
    charset takes the `.`, so the match runs past the mask literal, masking._matches()
    no longer reads it as its own output, and the tripwire filed wq#2650. That re-arm is
    ISSUE-LOG row 724's direction (1), and wq#2638 owns the fix. Split at the seams below
    so reading this file cannot fire the same alarm, and so account() finds the body here.
    """
    body = "REALSECRETVALUE123456" "cfut_" "MASKED" "."
    pin = (hashlib.sha256(body.encode()).hexdigest()[:12], len(body))
    assert pin == WQ2650_VALUE
    line = 'grep -o -n ".\\{60\\}' + "cfut_" + body + '\\{40\\}" kb/ISSUE-LOG.md'
    records = mask_replay.replay(0, tracked={}, items={"transcript:a": line})
    assert [(r["label"], r["value_sha256_12"], r["value_len"]) for r in records] \
        == [("cloudflare",) + WQ2650_VALUE]
    with open(__file__) as fh:
        here = fh.read()
    accounted = mask_replay.account(body, {"this": here})
    assert accounted is not None
    for seamed in (l for l in here.splitlines() if "REALSECRETVALUE" in l):
        assert masking.mask_text(seamed)[1] == [], seamed


WQ2687_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2687")
WQ2687_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "wq2687_values.py")


def _load_fixture_module(path):
    with open(path) as fh:
        text = fh.read()
    scope = {}
    exec(compile(text, path, "exec"), scope)
    return text, scope


def _unpinned(pins, tree=None):
    """The shas of the pins that don't hold, never their values (wq#2962).

    A bare `assert` over a pin tuple lets pytest's assertion rewrite print the dummy
    it compared: wq#2955 ran the red wq2913 pin test, `assert len(values) == 8` printed
    the whole tuple, and the tripwire burned three committed dummies. So every pin check
    goes through here and a failing test names hashes and counts only. A pin is
    (sha, label, value) or (sha, label, prefix, prefix + value); with `tree` the value
    must also be accounted there.
    """
    bad = []
    for pin in pins:
        sha, whole = pin[0], pin[-1]
        prefix = pin[2] if len(pin) == 4 else ""
        body = whole[len(prefix):]
        if not (whole.startswith(prefix) and masking.value_sha12(body) == sha
                and (tree is None or mask_replay.account(body, tree, prefix))):
            bad.append(sha)
    return bad


def _wq2687_values():
    text, scope = _load_fixture_module(WQ2687_FIXTURES)
    return text, scope["WQ2687_VALUES"]


def test_wq2687_pins_are_their_hashes_and_read_silently():
    """The pins name the values they claim to, and reading the file pages nothing."""
    text, scope = _load_fixture_module(WQ2687_FIXTURES)
    values, prose = scope["WQ2687_VALUES"], scope["WQ2687_PROSE"]
    counts = (len(values), len(prose))
    assert counts == (12, 2)
    assert _unpinned([*values, *prose]) == []
    assert masking.mask_text(text)[1] == []


def _assert_corpus_pinned(ticket_id, fixture_path, records_pin, fixture_shas,
                          patterns=None):
    """wq#2765 (wq#2755's shape): the pin is a hash SET, not a count or a path.

    Both `test_wq*_corpus_burned_nothing` pins score the live run dir and the live
    tree, and both moved with no code change: wq#2687's attempt 3 ran after its count
    was pinned (44 -> 47), and a second committed copy of wq#2702's dummy (d38a94d)
    meant popping the fixture module brought nothing back. Each drift erased the one
    signal the test carries, so the assertions are now the ones a new artefact
    cannot move: every pinned record is still reproduced from the corpus (a value the
    run saw has not silently left it), nothing is unaccounted, and with the fixture
    module removed whatever comes loose is a subset of what it pins — which it must
    explain on its own, so a second copy elsewhere in the tree changes nothing.
    `patterns` is the table the run is scored under: None (legacy) for a run before
    LEGACY_CUTOFF_ISO, CURRENT_PATTERNS for one after it (wq#2795).
    """
    items = mask_replay.corpus(ticket_id)
    tracked = _tree_only()
    for common in mask_replay.foreign_repos(items):
        tracked.update(mask_replay.committed_text(common))
    records = mask_replay.replay(ticket_id, patterns=patterns, tracked=tracked,
                                 items=items, rotated={})
    shas = {r["value_sha256_12"] for r in records}
    assert records_pin <= shas, sorted(records_pin - shas)
    assert fixture_shas <= shas, sorted(fixture_shas - shas)
    assert mask_replay.unaccounted(records) == []

    rel = os.path.relpath(fixture_path, mask_replay.REPO_ROOT)
    fixture_text = tracked.pop(rel)
    loose = mask_replay.unaccounted(
        mask_replay.replay(ticket_id, patterns=patterns, tracked=tracked, items=items,
                           rotated={}))
    loose_shas = {r["value_sha256_12"] for r in loose}
    assert loose_shas <= fixture_shas, sorted(loose_shas - fixture_shas)
    return records, {rel: fixture_text}


@pytest.mark.skipif(not os.path.isdir(WQ2687_RUN),
                    reason="wq#2687's run dir is not on this host")
def test_wq2687_corpus_burned_nothing():
    """wq#2701's verdict, pinned: none of the values a credential, nothing rotated.

    The row's own two kv-secret values (set d68eb822ead9) were already settled — one
    tracked, one rule-fixed. The twelve the replay still called BURNED? are wq#2687's
    unlanded fixtures, its branch name and two sequential `sed` probes, and two more
    are prose words glued to a prefix; see the fixture module's header. The module
    must explain each of them by itself (see _assert_corpus_pinned).
    """
    _, scope = _load_fixture_module(WQ2687_FIXTURES)
    values, prose = scope["WQ2687_VALUES"], scope["WQ2687_PROSE"]
    fixture_shas = {v[0] for v in values} | {p[0] for p in prose}
    _, fixture = _assert_corpus_pinned(2687, WQ2687_FIXTURES,
                                       scope["WQ2687_RECORDS"], fixture_shas)
    assert _unpinned([*values, *prose], fixture) == []


WQ2702_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2702")
WQ2702_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "wq2702_values.py")


def _wq2702_values():
    text, scope = _load_fixture_module(WQ2702_FIXTURES)
    return text, scope["WQ2702_VALUES"]


def test_wq2702_pins_are_their_hashes_and_read_silently():
    text, values = _wq2702_values()
    count = len(values)
    assert count == 1
    assert _unpinned(values) == []
    assert masking.mask_text(text)[1] == []


@pytest.mark.skipif(not os.path.isdir(WQ2702_RUN),
                    reason="wq#2702's run dir is not on this host")
def test_wq2702_corpus_burned_nothing():
    """wq#2706's verdict, pinned: none of the values a credential, nothing rotated.

    The one the replay called BURNED? is a probe wq#2702 typed into its own Write; see
    the fixture module's header. The module must explain it by itself — test_masking.py
    has quoted the same glue since d38a94d, which is why "removing the module brings
    it back" stopped being true (see _assert_corpus_pinned).
    """
    _, scope = _load_fixture_module(WQ2702_FIXTURES)
    values = scope["WQ2702_VALUES"]
    _, fixture = _assert_corpus_pinned(2702, WQ2702_FIXTURES, scope["WQ2702_RECORDS"],
                                       {v[0] for v in values})
    assert _unpinned(values, fixture) == []


WQ2720_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2720")
WQ2720_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "wq2720_values.py")


def test_wq2720_pins_are_their_hashes_and_read_silently():
    text, scope = _load_fixture_module(WQ2720_FIXTURES)
    values = scope["WQ2720_VALUES"]
    count = len(values)
    assert count == 1
    assert _unpinned(values) == []
    assert masking.mask_text(text)[1] == []


@pytest.mark.skipif(not os.path.isdir(WQ2720_RUN),
                    reason="wq#2720's run dir is not on this host")
def test_wq2720_corpus_burned_nothing():
    """wq#2795's verdict, pinned: none of the values a credential, nothing rotated.

    wq#2720 started after the legacy cutoff, so it replays under today's table. The one
    value the replay called BURNED? is a probe wq#2720 typed into its own command; see
    the fixture module's header, which must explain it by itself.
    """
    _, scope = _load_fixture_module(WQ2720_FIXTURES)
    values = scope["WQ2720_VALUES"]
    fixture_shas = {v[0] for v in values}
    _, fixture = _assert_corpus_pinned(2720, WQ2720_FIXTURES, scope["WQ2720_RECORDS"],
                                       fixture_shas,
                                       patterns=mask_replay.CURRENT_PATTERNS)
    assert _unpinned(values, fixture) == []


WQ2787_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2787")
WQ2787_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "wq2787_values.py")


def test_wq2787_pins_are_their_hashes_and_read_silently():
    text, scope = _load_fixture_module(WQ2787_FIXTURES)
    values = scope["WQ2787_VALUES"]
    count = len(values)
    assert count == 1
    assert _unpinned(values) == []
    assert masking.mask_text(text)[1] == []


@pytest.mark.skipif(not os.path.isdir(WQ2787_RUN),
                    reason="wq#2787's run dir is not on this host")
def test_wq2787_corpus_burned_nothing():
    """wq#2839's verdict, pinned: the one BURNED? value is a probe wq#2787 typed, not
    a credential, nothing rotated. Scored under today's table (started after the
    legacy cutoff); the fixture module's header must explain it by itself.
    """
    _, scope = _load_fixture_module(WQ2787_FIXTURES)
    values = scope["WQ2787_VALUES"]
    fixture_shas = {v[0] for v in values}
    _, fixture = _assert_corpus_pinned(2787, WQ2787_FIXTURES, scope["WQ2787_RECORDS"],
                                       fixture_shas,
                                       patterns=mask_replay.CURRENT_PATTERNS)
    assert _unpinned(values, fixture) == []


def test_replay_reads_what_the_tripwire_reads_on_a_glued_line():
    """wq#2875: one finditer per rule missed the aws and slack values a truncated github
    match frees on a later pass, so wq#2848's replay never scored its own ticket's hits."""
    line = ("ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8" + "AKI" + "A" + "Q" * 16
            + "xox" + "b-" + "1234567890" + "1")
    live = [(r["label"], r["value_sha256_12"]) for r in masking.scan(line)[1]]
    # wq#2913: records follow PATTERNS order (slack is listed before aws) rather than
    # the order a multi-pass sweep happened to free each value in.
    assert [label for label, _ in live] == ["github", "slack", "aws"]
    records = mask_replay.replay(0, patterns=mask_replay.CURRENT_PATTERNS, tracked={},
                                 items={"transcript:probe": line}, rotated={})
    assert [(r["label"], r["value_sha256_12"]) for r in records] == live


WQ2848_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2848")


@pytest.mark.skipif(not os.path.isdir(WQ2848_RUN),
                    reason="wq#2848's run dir is not on this host")
def test_wq2848_replay_reproduces_every_live_hit():
    """wq#2875's verdict, pinned: every value the tripwire recorded (github, aws, slack,
    all from one glued `printf` probe) is reproduced by the replay and accounted for."""
    with open(os.path.join(WQ2848_RUN, "mask-hits-1.json")) as fh:
        fired = {r["value_sha256_12"] for r in json.load(fh) if "suppressed" not in r}
    assert fired == {"76b4100ba253", "e69bbe113521", "254aa248acb4"}
    records = mask_replay.replay(2848, patterns=mask_replay.CURRENT_PATTERNS, rotated={})
    assert fired <= {r["value_sha256_12"] for r in records}
    assert mask_replay.unaccounted(records) == []


WQ2875_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "wq2875_values.py")


def test_wq2875_pins_are_their_hashes_and_read_silently():
    text, scope = _load_fixture_module(WQ2875_FIXTURES)
    assert _unpinned(scope["WQ2875_VALUES"], {"f": text}) == []
    assert masking.mask_text(text)[1] == []


WQ2913_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2913")
WQ2913_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "wq2913_values.py")


def test_wq2913_pins_are_their_hashes_and_read_silently():
    """wq#2955: coverage of row 924's eight, not a count of one set.

    A count was the wrong invariant twice over. wq#2936 hardcoded
    `len(WQ2913_VALUES) == 8` against a 7-tuple fixture and shipped red; wq#2962 took
    it down to 7 to match what was there, which made the test green by agreeing with
    the omission — sha 41964b438456 is its own replay record, not a duplicate the kb
    log had already covered, and `_assert_corpus_pinned`'s pop-check frees it and finds
    it in no pin. Meanwhile a count says nothing at all about the thing that actually
    broke: which set a value belongs to, which the rule table decides and keeps
    changing.

    So the pin is coverage. Row 924's eight hashes are a closed historical fact — what
    wq#2913's live tripwire reported — and every one of them must still be pinned by
    one of the two sets. A rule change may move a tuple between the sets, or add a
    re-cut hash to WQ2913_VALUES (960404a added three), but it can never drop one of
    the eight out of the file without failing here.
    """
    text, scope = _load_fixture_module(WQ2913_FIXTURES)
    values, retired = scope["WQ2913_VALUES"], scope["WQ2913_RULE_RETIRED"]
    pinned = {v[0] for v in values}
    assert not pinned & {v[0] for v in retired}
    assert scope["WQ2913_ROW_924"] <= pinned | {v[0] for v in retired}
    assert _unpinned(values + retired, {"f": text}) == []
    assert masking.mask_text(text)[1] == []


def test_a_failing_pin_names_hashes_not_values():
    """wq#2962: the tuple pytest printed for wq#2955 fires the tripwire; what a failing
    pin check prints now does not, and still says which pin broke."""
    text, scope = _load_fixture_module(WQ2913_FIXTURES)
    values = scope["WQ2913_VALUES"]
    assert masking.mask_text(repr(values))[1] != []
    broken = [("0" * 12,) + values[0][1:], *values[1:]]
    report = _unpinned(broken, {"f": text})
    assert report == ["0" * 12]
    assert masking.mask_text(repr(report))[1] == []


def test_wq2913_retired_pins_are_not_cut_by_todays_table():
    """wq#2955: the pins wq#2936 could not keep, kept as the reason it could not.

    Two rule changes retired seven of row 924's eight between wq#2913's run and now:
    05e65ce (wq#2931) taught url-auth to refuse a `<name>` placeholder body, and
    960404a (wq#2960) let the rules take the union of their spans, so the three pytest
    ids read as slack and the three probe outputs end in the mask literal and are
    dropped. wq#2936 asked the replay for hashes the table had just stopped producing.

    Asserting the refusal by HASH, not by an empty scan, is what makes the split
    reversible and precise: the three pytest-id lines are still cut, just into a
    different value (the re-cut `mask_replay.replay` calls `today_closed`), so `scan()
    == []` would be false for them and true for the others for unrelated reasons.
    Narrow either rule and the retired hash reappears here, naming the tuple to move
    back into WQ2913_VALUES. The one tuple this is weak for is the url-auth pin, whose
    rule anchors on an `@host` the pinned string stops short of, so scanning it alone
    would fire on nothing whatever wq#2931 did; that one is carried by
    test_wq2913_corpus_burned_nothing, which replays the corpus line in full.
    """
    _, scope = _load_fixture_module(WQ2913_FIXTURES)
    for sha, _label, _prefix, whole in scope["WQ2913_RULE_RETIRED"]:
        assert sha not in {h["value_sha256_12"] for h in masking.scan(whole)[1]}, sha


@pytest.mark.skipif(not os.path.isdir(WQ2913_RUN),
                    reason="wq#2913's run dir is not on this host")
def test_wq2913_corpus_burned_nothing():
    """wq#2936's verdict, pinned: pytest ids, probe output and a docstring placeholder
    from wq#2913's own work, not credentials; nothing rotated. wq#2955: seven of the
    eight are pinned by the test above instead — today's table does not cut them, so
    the replay cannot reproduce them, and asserting their absence here is what keeps
    the two sets from silently drifting back together.
    """
    _, scope = _load_fixture_module(WQ2913_FIXTURES)
    fixture_shas = {v[0] for v in scope["WQ2913_VALUES"]}
    records, _ = _assert_corpus_pinned(2913, WQ2913_FIXTURES, scope["WQ2913_RECORDS"],
                                       fixture_shas,
                                       patterns=mask_replay.CURRENT_PATTERNS)
    shas = {r["value_sha256_12"] for r in records}
    assert not shas & {v[0] for v in scope["WQ2913_RULE_RETIRED"]}


WQ2922_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq2922")
WQ2922_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "wq2922_values.py")


def test_wq2922_pins_are_their_hashes_and_read_silently():
    text, scope = _load_fixture_module(WQ2922_FIXTURES)
    values = scope["WQ2922_VALUES"]
    count = len(values)
    assert count == 3
    assert _unpinned(values, {"f": text}) == []
    assert masking.mask_text(text)[1] == []


@pytest.mark.skipif(not os.path.isdir(WQ2922_RUN),
                    reason="wq#2922's run dir is not on this host")
def test_wq2922_corpus_burned_nothing():
    """wq#2952's verdict, pinned: a comment word and a Q-filled probe dummy from
    wq#2922's own work, not credentials; nothing rotated.

    wq#2982: a third value joined the pin later without a new run — wq#2972 split
    reports/wq2941_probe_self_name_punctuation_bypass.py's `REAL_SECRET` literal to
    stop it arming its own tripwire, which left the snapshot's pre-split 11-char first
    segment unfindable in the post-split tree. See the fixture module's header.
    """
    _, scope = _load_fixture_module(WQ2922_FIXTURES)
    fixture_shas = {v[0] for v in scope["WQ2922_VALUES"]}
    _assert_corpus_pinned(2922, WQ2922_FIXTURES, scope["WQ2922_RECORDS"], fixture_shas,
                          patterns=mask_replay.CURRENT_PATTERNS)
