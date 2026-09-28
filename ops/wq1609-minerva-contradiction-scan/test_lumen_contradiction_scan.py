#!/usr/bin/env python3
"""Tests for lumen_contradiction_scan (wq#1609)."""
import importlib.util
import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "lcs", os.path.join(HERE, "lumen_contradiction_scan.py")
)
lcs = importlib.util.module_from_spec(spec)
sys.modules["lcs"] = lcs
spec.loader.exec_module(lcs)


SCHEMA = """
create table state_facts (
    id TEXT PRIMARY KEY, subject TEXT, predicate TEXT, value TEXT,
    valid_from TEXT, valid_to TEXT, ingested_at TEXT, invalidated_at TEXT,
    source_ref TEXT, confidence REAL, episode_id TEXT, half_life_days REAL,
    last_confirmed_at TEXT, fact_class TEXT, render_tier TEXT);
create table archival_notes (
    id TEXT PRIMARY KEY, kind TEXT, title TEXT, content TEXT, embedding BLOB,
    tags TEXT, source_ref TEXT, created_at TEXT, origin TEXT, trust TEXT,
    agent_id TEXT, lane TEXT, content_sha256 TEXT, schema_version INTEGER,
    supersedes TEXT, superseded_by TEXT, revoked_at TEXT, revoked_reason TEXT);
"""

FACTS = [
    # id, subject, predicate, value, valid_to, fact_class
    ("f1", "nephthys", "role", "embeddings-host", "2026-09-01", "identity"),
    ("f2", "nephthys", "role", "DECOMMISSIONED", None, "identity"),
    ("f3", "thoth", "tailscale_ip", "100.88.71.47", "2026-08-20", "config"),
    ("f4", "thoth", "tailscale_ip", "100.88.71.99", None, "config"),
    ("f5", "pbox", "status", "active", "2026-08-05", "status"),
    ("f6", "pbox", "status", "DECOMMISSIONED", None, "status"),
    ("f7", "hathor", "power_plug", "P3", "2026-07-01", "config"),
    ("f8", "hathor", "power_plug", "P5", None, "config"),
    ("f9", "lumen", "root_disk",
     "227G/466G 51% used, 220G free after the 2026-08-09 offload (was 365G/82%)",
     "2026-09-08", "narrative"),
    ("f10", "ddr4_2933_32g", "price", "133", "2026-09-09", "config"),
    ("f11", "isis", "unit", "lumen-brain.service", "2026-08-15", "config"),
    ("f12", "isis", "unit", "lumen-brain2.service", None, "config"),
    ("f13", "isis", "service_state", "UP", "2026-09-05", "status"),
    ("f14", "isis", "service_state", "DEGRADED", None, "status"),
    ("f15", "hathor", "fan_state", "AUTO", "2026-08-10", "status"),
    ("f16", "hathor", "fan_state", "ON", None, "status"),
]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "minerva.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for fid, subj, pred, val, valid_to, fclass in FACTS:
        conn.execute(
            "insert into state_facts (id, subject, predicate, value, valid_from,"
            " valid_to, ingested_at, fact_class) values (?,?,?,?,?,?,?,?)",
            (fid, subj, pred, val, "2026-06-01", valid_to, "2026-06-01", fclass),
        )
    conn.commit()
    conn.close()
    return str(path)


def add_note(db_path, note_id, content, title="", **kw):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "insert into archival_notes (id, title, content, revoked_at, superseded_by)"
        " values (?,?,?,?,?)",
        (note_id, title, content, kw.get("revoked_at"), kw.get("superseded_by")),
    )
    conn.commit()
    conn.close()


def index_of(db_path):
    conn = lcs.connect(db_path, readonly=True)
    try:
        return lcs.build_dead_value_index(conn)
    finally:
        conn.close()


def values(index):
    return {e["dead_value"] for e in index}


# ------------------------------------------------------------------- the index

def add_fact(db_path, fid, subj, pred, val, valid_to, fclass):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "insert into state_facts (id, subject, predicate, value, valid_from,"
        " valid_to, ingested_at, fact_class) values (?,?,?,?,?,?,?,?)",
        (fid, subj, pred, val, "2026-06-01", valid_to, "2026-06-01", fclass),
    )
    conn.commit()
    conn.close()


def test_value_still_open_under_a_renamed_predicate_is_not_dead(db):
    # wq#1782: gameboy.ts_ip closed in a bulk close, gameboy.tailscale_ip
    # still holds the same address.
    add_fact(db, "g1", "gameboy", "ts_ip", "100.118.56.59", "2026-09-10", "config")
    add_fact(db, "g2", "gameboy", "tailscale_ip", "100.118.56.59", None, "config")
    assert "100.118.56.59" not in values(index_of(db))


def test_renamed_predicate_rule_is_scoped_to_the_same_subject(db):
    add_fact(db, "g1", "gameboy", "ts_ip", "100.118.56.59", "2026-09-10", "config")
    add_fact(db, "g2", "kiosk", "tailscale_ip", "100.118.56.59", None, "config")
    assert "100.118.56.59" in values(index_of(db))


def test_renamed_predicate_rule_does_not_rescue_bare_labels(db):
    add_fact(db, "g1", "gameboy", "status", "active", "2026-09-10", "status")
    add_fact(db, "g2", "gameboy", "service_state", "active", None, "status")
    assert ("gameboy", "status") in {
        (e["subject"], e["predicate"]) for e in index_of(db)
    }


def test_index_reaches_beyond_ip_shaped_values(db):
    got = values(index_of(db))
    assert {"embeddings-host", "100.88.71.47", "active", "P3",
            "lumen-brain.service"} <= got


def test_index_excludes_narrative_prose_and_volatile_predicates(db):
    got = values(index_of(db))
    assert not any(v.startswith("227G/466G") for v in got)
    assert "133" not in got


def test_index_carries_the_current_value_for_each_closed_fact(db):
    entry = next(e for e in index_of(db) if e["dead_value"] == "embeddings-host")
    assert entry["current_value"] == "DECOMMISSIONED"
    assert entry["subject"] == "nephthys"


def test_self_identifying_values_are_not_subject_scoped(db):
    idx = index_of(db)
    for value in ("100.88.71.47", "lumen-brain.service", "embeddings-host"):
        assert next(e for e in idx if e["dead_value"] == value)["scoped"] is False


def test_bare_labels_and_common_words_stay_subject_scoped(db):
    idx = index_of(db)
    for value in ("active", "P3"):
        assert next(e for e in idx if e["dead_value"] == value)["scoped"] is True


def test_value_open_on_two_subjects_is_subject_scoped_despite_looking_self_identifying(db):
    """wq#2850: a kernel string has digits and dots so self_identifying() says
    True, but nephthys.yaml:22 quoting '7.0.0-30-generic' was flagged as
    sobek's dead kernel although sobek is never named in the file. sobek closing
    -30 doesn't make -30 sobek's alone: nephthys and thoth are both still open
    on it, so two live subjects sharing the string proves it's fleet
    vocabulary, not an identifier."""
    add_fact(db, "k1", "sobek", "kernel", "7.0.0-30-generic", "2026-09-26", "config")
    add_fact(db, "k2", "sobek", "kernel", "7.0.0-34-generic", None, "config")
    add_fact(db, "k3", "nephthys", "kernel", "7.0.0-30-generic", None, "config")
    add_fact(db, "k4", "thoth", "kernel", "7.0.0-30-generic", None, "config")
    entry = next(e for e in index_of(db) if e["dead_value"] == "7.0.0-30-generic")
    assert entry["scoped"] is True


def test_value_open_on_exactly_one_other_subject_is_subject_scoped(db):
    """wq#2878: wq#2850 shipped a `>=2 open subjects => shared` threshold, but
    wq#2812's own incident had exactly ONE other node on the dead build ('any
    node still legitimately on -30 ... now false-positives'), so the threshold
    sat above the bug's minimum reproduction and it kept firing. One other live
    subject is already proof the value isn't an identifier."""
    add_fact(db, "k1", "sobek", "kernel", "7.0.0-30-generic", "2026-09-26", "config")
    add_fact(db, "k2", "sobek", "kernel", "7.0.0-31-generic", None, "config")
    add_fact(db, "k3", "nephthys", "kernel", "7.0.0-30-generic", None, "config")
    entry = next(e for e in index_of(db) if e["dead_value"] == "7.0.0-30-generic")
    assert entry["scoped"] is True
    assert scan_line(db, "kernel: '7.0.0-30-generic'") == []


def test_one_sharer_shared_value_still_flags_a_line_naming_the_closed_subject(db):
    """Positive control for the N=1 case: scoping must still be able to fire."""
    add_fact(db, "k1", "sobek", "kernel", "7.0.0-30-generic", "2026-09-26", "config")
    add_fact(db, "k2", "sobek", "kernel", "7.0.0-31-generic", None, "config")
    add_fact(db, "k3", "nephthys", "kernel", "7.0.0-30-generic", None, "config")
    hits = scan_line(db, "sobek kernel: 7.0.0-30-generic")
    assert [(h["subject"], h["predicate"]) for h in hits] == [("sobek", "kernel")]


def test_shared_value_predicates_are_scoped_with_no_live_sharer_at_all(db):
    """wq#2878: a state-store census alone leaves the class open in its steady
    state. Once a fleet-wide bump closes -30 on every node, nobody is open on it,
    the sharer count is zero, and a kernel string is no more of an identifier
    than it was mid-rollout — so the predicate classes wq#2850 named (kernel, os,
    *_version, bios, model) are scoped by construction, not by census."""
    add_fact(db, "k1", "sobek", "kernel", "7.0.0-30-generic", "2026-09-26", "config")
    add_fact(db, "k2", "sobek", "kernel", "7.0.0-34-generic", None, "config")
    entry = next(e for e in index_of(db) if e["dead_value"] == "7.0.0-30-generic")
    assert entry["scoped"] is True
    assert scan_line(db, "kernel: '7.0.0-30-generic'") == []
    assert [h["predicate"] for h in scan_line(db, "sobek kernel 7.0.0-30-generic")] \
        == ["kernel"]


@pytest.mark.parametrize(
    "predicate,shared",
    [("kernel", True), ("kernel_version", True), ("os", True), ("os_version", True),
     ("bios", True), ("firmware_rev", True), ("model", True), ("agent_version", True),
     ("tailscale_ip", False), ("unit", False), ("power_plug", False), ("role", False),
     ("osd_count", False), ("hostname", False)],
)
def test_shared_value_predicate_pattern(predicate, shared):
    assert bool(lcs.SHARED_VALUE_PREDICATE_RE.search(predicate)) is shared


def test_a_unique_self_identifying_value_stays_unscoped(db):
    """The other side of the trade: an address nobody else holds, under a
    predicate that is not shared vocabulary, must still match anywhere — the
    wq#2878 widening may not turn the scan off."""
    entry = next(e for e in index_of(db) if e["dead_value"] == "100.88.71.47")
    assert entry["scoped"] is False
    assert [h["subject"] for h in scan_line(db, "ssh 100.88.71.47")] == ["thoth"]


def test_a_shouted_status_word_is_not_self_identifying():
    """wq#1629 split prose status values into tokens, so the index is now full
    of words like RETIRED. Uppercase says nothing about WHICH subject is
    retired, so these match subject-scoped like their lowercase twins —
    unscoping them took the live scan from 14 to 777 hits."""
    assert lcs.self_identifying("100.88.71.47")
    assert lcs.self_identifying("lumen-brain.service")
    assert lcs.self_identifying("embeddings-host")
    for word in ("RETIRED", "OFFLINE", "DECOMMISSIONED"):
        assert not lcs.self_identifying(word)


def test_a_shouted_hyphenated_compound_is_not_self_identifying():
    """wq#1781: a hyphen alone doesn't carry a subject either. POWERED-DOWN is
    two dictionary words joined by punctuation, not an identifier — it should
    stay subject-scoped exactly like RETIRED, while a real hyphenated
    identifier (embeddings-host) keeps self-identifying."""
    for word in ("POWERED-DOWN", "POWERED-ON", "LIVE-VERIFIED", "TORN-DOWN",
                 "FULLY-RETIRED"):
        assert not lcs.self_identifying(word)
    assert lcs.self_identifying("embeddings-host")
    assert lcs.self_identifying("lumen-brain.service")


def test_common_word_values_are_recognised_by_word_not_by_length():
    """wq#2151: the risky class is 'the value is also ordinary English', not
    'the value is short'. P3 and OFFLINE are the control: two characters and
    eight, neither of them a word prose uses without meaning the status."""
    for word in ("UP", "up", "down", "on", "off", "new", "old", "free", "live"):
        assert lcs.common_word(word)
    for token in ("P3", "OFFLINE", "DECOMMISSIONED", "embeddings-host",
                  "100.88.71.47", "AUTO"):
        assert not lcs.common_word(token)


def test_common_word_values_match_case_exactly():
    assert lcs.boundary_re("UP", case_exact=True).search("service_state: UP")
    assert not lcs.boundary_re("UP", case_exact=True).search("set up the container")
    assert lcs.boundary_re("OFFLINE").search("hathor is offline")


# ------------------------------------------------------------------- matching

def scan_line(db_path, line):
    return lcs.scan_text(line, index_of(db_path), "t:1", lcs.TIER_PROPOSAL)


def test_ip_hit_needs_no_subject_on_the_line(db):
    hits = scan_line(db, "ssh into 100.88.71.47 and check the queue")
    assert [h["dead_value"] for h in hits] == ["100.88.71.47"]
    assert hits[0]["severity"] == "action"


def test_token_boundaries_reject_partial_matches(db):
    assert scan_line(db, "the range 100.88.71.470 is unrelated") == []
    assert scan_line(db, "pbox is inactive right now") == []


def test_generic_value_requires_its_subject_on_the_line(db):
    assert scan_line(db, "the printer status is active") == []
    hits = scan_line(db, "pbox status is active and serving telemetry")
    assert [h["subject"] for h in hits] == ["pbox"]


def test_generic_value_requires_a_predicate_cue(db):
    assert scan_line(db, "pbox kept the reflector timer enabled and active") == []
    assert scan_line(db, "thor: the dead 24-port switch was reflashed") == []
    assert [h["predicate"] for h in scan_line(db, "hathor power_plug: P3")] \
        == ["power_plug"]


def test_shared_kernel_string_does_not_flag_a_file_that_never_names_the_closed_subject(db):
    """wq#2812/wq#2850 regression: nephthys.yaml quotes the shared kernel
    build sobek closed, but never mentions sobek (grep -c sobek == 0 on the
    real file). Every node still legitimately on -30 must not false-positive
    on sobek's supersession."""
    add_fact(db, "k1", "sobek", "kernel", "7.0.0-30-generic", "2026-09-26", "config")
    add_fact(db, "k2", "sobek", "kernel", "7.0.0-34-generic", None, "config")
    add_fact(db, "k3", "nephthys", "kernel", "7.0.0-30-generic", None, "config")
    add_fact(db, "k4", "thoth", "kernel", "7.0.0-30-generic", None, "config")
    assert scan_line(db, "kernel: '7.0.0-30-generic'") == []


def test_shared_kernel_string_still_flags_a_line_naming_the_closed_subject(db):
    """Positive control: a line that actually claims sobek is still on the
    dead kernel build must still flag — subject scoping must be able to
    fail, not just always pass (wq#2630)."""
    add_fact(db, "k1", "sobek", "kernel", "7.0.0-30-generic", "2026-09-26", "config")
    add_fact(db, "k2", "sobek", "kernel", "7.0.0-34-generic", None, "config")
    add_fact(db, "k3", "nephthys", "kernel", "7.0.0-30-generic", None, "config")
    add_fact(db, "k4", "thoth", "kernel", "7.0.0-30-generic", None, "config")
    hits = scan_line(db, "sobek kernel: 7.0.0-30-generic")
    assert [h["subject"] for h in hits] == ["sobek"]


def scan_in_section(db_path, line, section):
    return lcs.scan_text(line, index_of(db_path), "t:1", lcs.TIER_PROPOSAL,
                         subject_context=section)


def test_english_prose_does_not_match_a_common_word_value(db):
    """The eight of nine corpus hits wq#2151 was filed for (§8.1): isis's
    service_state closed UP, and every hit was the preposition 'up' in a
    research section that happened to name Isis and a service. wq#1775 narrowed
    this for the corpus tier; these lines go through the *fleet* matcher, where
    the same four false positives were measured in the daily scan."""
    section = "## Isis service plan\nisis hosts the embedding service\n"
    for line in ("Spin up a **FastAPI** orchestrator for the Isis service",
                 "6. **Set up the Docker container** for the memory service",
                 "Deploy the micro-service on **Isis** and bring it up"):
        assert scan_in_section(db, line, section) == []


def test_common_word_value_hits_when_the_line_quotes_the_status(db):
    hits = scan_in_section(db, "isis service_state: UP", "")
    assert [(h["subject"], h["dead_value"]) for h in hits] == [("isis", "UP")]


def test_common_word_value_keeps_its_inherited_cue_and_subject(db):
    """Only case narrows here. A common-word value still takes its subject and
    its predicate cue from the enclosing section, like every other bare label —
    the line-local cue rule wq#2151 also weighed was dropped: it removed one
    further corpus hit that case already removes, and it would have silenced
    `# pbox status` / `the scheduler stayed active` in a fleet doc."""
    section = "## isis service_state\n"
    assert [h["dead_value"] for h in
            scan_in_section(db, "isis reported UP again", section)] == ["UP"]


def test_changelog_suppressor_respects_the_case_of_a_common_current_value(db):
    """hathor.fan_state is now `ON`, so a line quoting the dead `AUTO` is still
    a contradiction when the only `on` in it is the English preposition."""
    hits = scan_line(db, "hathor fan_state AUTO, on the rear intake")
    assert [h["dead_value"] for h in hits] == ["AUTO"]
    assert scan_line(db, "hathor fan_state AUTO -> ON since 2026-08-10") == []


def test_narrative_value_is_never_string_matched(db):
    assert scan_line(db, "lumen root_disk 227G/466G 51% used, 220G free") == []


def test_hit_reports_fact_provenance(db):
    hit = scan_line(db, "nephthys serves as the embeddings-host for the fleet")[0]
    assert (hit["subject"], hit["predicate"]) == ("nephthys", "role")
    assert hit["current_value"] == "DECOMMISSIONED"
    assert hit["valid_to"] == "2026-09-01"
    assert hit["fact_id"] == "f1"


# ----------------------------------------------------------------- suppressors

def test_negation_suppressor(db):
    assert scan_line(db, "nephthys was the embeddings-host until August") == []
    assert scan_line(db, "100.88.71.47 is no longer routable") == []


def test_changelog_suppressor_current_value_on_the_same_line(db):
    assert scan_line(db, "thoth 100.88.71.47 -> 100.88.71.99 during the migration") == []
    assert scan_line(db, "isis runs lumen-brain.service / lumen-brain2.service") == []


def test_changelog_suppressor_is_scoped_to_the_same_line(db):
    text = "isis runs lumen-brain.service today.\nlumen-brain2.service is planned."
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        hits += lcs.scan_text(line, index_of(db), f"t:{n}", lcs.TIER_PROPOSAL)
    assert [h["dead_value"] for h in hits] == ["lumen-brain.service"]


def test_as_of_suppressor_downgrades_dated_history_to_informational(db):
    hit = scan_line(db, "2026-07-04 audit: nephthys is the embeddings-host")[0]
    assert hit["severity"] == "info"


def test_as_of_suppressor_leaves_later_text_actionable(db):
    hit = scan_line(db, "2026-09-09 audit: nephthys is the embeddings-host")[0]
    assert hit["severity"] == "action"


# -------------------------------------------------------- artifacts and tiers

def test_file_artifacts_carry_their_action_tier(db, tmp_path):
    memory = tmp_path / "claude_memory.md"
    memory.write_text("Nephthys is the embeddings-host.\nthoth at 100.88.71.47\n")
    manifest = tmp_path / "storage_manifest.yaml"
    manifest.write_text("hathor:\n  power_plug: P3\n")
    hits = lcs.scan_files(
        index_of(db),
        artifacts=((str(memory), lcs.TIER_PROPOSAL_ONLY),
                   (str(manifest), lcs.TIER_TICKET)),
    )
    tiers = {h["tier"] for h in hits}
    assert tiers == {lcs.TIER_PROPOSAL_ONLY, lcs.TIER_TICKET}
    assert any(h["location"].endswith(":1") for h in hits)


def test_yaml_value_inherits_the_subject_from_its_block_key(db, tmp_path):
    manifest = tmp_path / "storage_manifest.yaml"
    manifest.write_text("hathor:\n  power:\n    power_plug: P3\n")
    hits = lcs.scan_files(index_of(db), artifacts=((str(manifest), lcs.TIER_TICKET),))
    assert [(h["subject"], h["dead_value"]) for h in hits] == [("hathor", "P3")]


def test_a_common_word_value_is_read_in_the_case_the_yaml_wrote_it(db, tmp_path):
    """A YAML artifact that shouts the dead value still reports; one that uses
    the word in a prose field does not. This is the `bes.yaml` shape from the
    live scan: `alert_desc: "… vault standby slot down"` against a dead
    `status = DOWN` (wq#2151)."""
    quoted = tmp_path / "storage_manifest.yaml"
    quoted.write_text("isis:\n  service_state: UP\n")
    hits = lcs.scan_files(index_of(db), artifacts=((str(quoted), lcs.TIER_TICKET),))
    assert [(h["subject"], h["dead_value"]) for h in hits] == [("isis", "UP")]

    prose = tmp_path / "machines.yaml"
    prose.write_text('isis:\n  service_state:\n    alert: "bring it up after"\n')
    assert lcs.scan_files(index_of(db),
                          artifacts=((str(prose), lcs.TIER_TICKET),)) == []


def test_block_context_does_not_reach_a_sibling_block(db, tmp_path):
    manifest = tmp_path / "storage_manifest.yaml"
    manifest.write_text("hathor:\n  power_plug: P5\nseshat:\n  power_plug: P3\n")
    assert lcs.scan_files(index_of(db),
                          artifacts=((str(manifest), lcs.TIER_TICKET),)) == []


def test_notes_are_scanned_and_revoked_notes_are_skipped(db):
    add_note(db, "n1", "nephthys is the embeddings-host for recall")
    add_note(db, "n2", "nephthys is the embeddings-host", revoked_at="2026-09-02")
    add_note(db, "n3", "nephthys is the embeddings-host", superseded_by="n1")
    conn = lcs.connect(db, readonly=True)
    hits = lcs.scan_notes(conn, index_of(db))
    conn.close()
    assert [h["location"] for h in hits] == ["archival_notes:n1"]
    assert hits[0]["tier"] == lcs.TIER_NOTE


# ------------------------------------------------------------------ end to end

def test_scan_reports_reach_versus_the_old_ip_only_filter(db, tmp_path):
    add_note(db, "n1", "nephthys is the embeddings-host for recall")
    result = lcs.scan(db, artifacts=())
    assert result["closed_facts"] == 9
    assert result["index_size"] == 7
    assert result["ip_only_size"] == 1
    assert result["actionable"] == 1
    assert "note_row" in lcs.render(result)


def test_apply_writes_note_contradiction_rows_idempotently(db):
    add_note(db, "n1", "nephthys is the embeddings-host for recall")
    first = lcs.scan(db, apply=True, artifacts=())
    assert first["actions"]["note_contradictions"] == 1
    second = lcs.scan(db, apply=True, artifacts=())
    assert second["actions"]["note_contradictions"] == 0
    conn = sqlite3.connect(db)
    row = conn.execute(
        "select note_id, subject, predicate, dead_value, current_value, severity"
        " from note_contradictions"
    ).fetchall()
    conn.close()
    assert row == [("n1", "nephthys", "role", "embeddings-host",
                    "DECOMMISSIONED", "action")]


def test_dry_run_never_opens_the_database_for_writing(db):
    add_note(db, "n1", "nephthys is the embeddings-host for recall")
    lcs.scan(db, artifacts=())
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute(
        "select name from sqlite_master where type='table'")}
    conn.close()
    assert "note_contradictions" not in tables


def test_cli_json_output_and_exit_codes(db, capsys):
    add_note(db, "n1", "nephthys is the embeddings-host for recall")
    assert lcs.main(["--db", db, "--json", "--no-files"]) == 0
    payload = capsys.readouterr().out
    assert '"index_size": 7' in payload
    assert lcs.main(["--db", db, "--strict", "--no-files"]) == 1
    assert lcs.main(["--db", "/nonexistent/minerva.db", "--no-files"]) == 2


# ----------------------------------------------------------- corpus (wq#1775)

class FakeCursor:
    """Enough psycopg2 to exercise the corpus tier without Postgres: ILIKE is
    case-insensitive containment, the chunk lookup is a span search, and
    doc_contradictions enforces its UNIQUE(doc_id, fact_id, char_start)."""

    def __init__(self, pg):
        self.pg = pg
        self._rows = []
        self.rowcount = -1

    def execute(self, sql, params=()):
        self.pg.sql.append((" ".join(sql.split()), params))
        low = sql.lower()
        if "from research_docs" in low:
            needles = [p.strip("%").lower() for p in params]
            self._rows = [
                (d[0], d[1], d[2]) for d in self.pg.docs
                if all(n in (d[2] or "").lower() for n in needles)
            ]
        elif "from research_chunks" in low:
            doc_id, char_start = params
            self._rows = [
                (ix,) for (did, ix, start, end) in self.pg.chunks
                if did == doc_id and start <= char_start <= end
            ]
        elif low.startswith("insert into doc_contradictions"):
            key = (params[0], params[2], params[9])
            if key in self.pg.written_keys:
                self.rowcount = 0
            else:
                self.pg.written_keys.add(key)
                self.pg.rows.append(params)
                self.rowcount = 1
        else:
            self._rows = []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakePG:
    def __init__(self, docs, chunks=()):
        self.docs, self.chunks = docs, list(chunks)
        self.sql, self.rows, self.written_keys = [], [], set()
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def close(self):
        self.closed = True


FLEET_DOC = "/home/lumen/foreman/research/DISPATCH_REVIEW.md"
PROSE_DOC = "/home/lumen/scratch/research_clean/intel_routing_survey_ab12.md"


def corpus_hits(db, docs, chunks=(), monkeypatch=None, entry_values=None):
    index = [e for e in index_of(db)
             if entry_values is None or e["dead_value"] in entry_values]
    pg = FakePG(docs, chunks)
    hits, _ = lcs.scan_corpus(pg.cursor(), index)
    return hits, pg


def test_doc_kind_comes_from_the_path_never_the_content():
    assert lcs.doc_kind(FLEET_DOC) == "fleet_internal"
    assert lcs.doc_kind("/home/lumen/agents/work-queue/reports/r.md") == "fleet_internal"
    assert lcs.doc_kind(PROSE_DOC) == "external_literature"
    assert lcs.doc_kind("/x/news_ups_review_99.md") == "external_literature"
    assert lcs.doc_kind("/home/lumen/scratch/research_clean/pe_summary.md") == "generated"


def test_a_plain_corpus_assertion_is_still_a_hit(db):
    hits, _ = corpus_hits(db, [(7, PROSE_DOC, "pbox status: active today\n")])
    assert [(h["subject"], h["dead_value"], h["tier"]) for h in hits] == [
        ("pbox", "active", "doc_row")]


def test_hypothetical_mood_is_suppressed_in_the_corpus(db):
    for line in (
        "if pbox status were active the router would retry\n",
        "the fraction of buckets where pbox status is active\n",
        "e.g. a pbox status of active means the node answers\n",
    ):
        hits, _ = corpus_hits(db, [(7, PROSE_DOC, line)])
        assert hits == [], line


def test_a_failure_mode_table_row_is_not_an_assertion(db):
    row = "| Node failure – pbox status active. | Pipeline stalls. |\n"
    hits, _ = corpus_hits(db, [(7, PROSE_DOC, row)])
    assert hits == []
    # The same row without failure-mode vocabulary is an ordinary table cell.
    hits, _ = corpus_hits(db, [(7, PROSE_DOC, "| pbox | status | active |\n")])
    assert len(hits) == 1


def test_the_hypothetical_suppressor_is_corpus_only(db, tmp_path):
    """The fleet tiers keep exactly wq#1609's three suppressors: a conditional
    line in an owned artifact still reports, because narrowing them was never
    measured there."""
    path = tmp_path / "FLEET_MAP.md"
    path.write_text("if pbox status is active the router retries\n")
    hits = lcs.scan_files(index_of(db), artifacts=((str(path), lcs.TIER_PROPOSAL),))
    assert len(hits) == 1


def test_a_short_shouted_value_must_be_shouted_in_the_prose_too(db):
    """Measured 2026-09-20: all four hits that survived the first two
    narrowings were isis.service_state='UP' matching the English `up`.
    The fixture's f13/f14 are that fact."""
    english = "Spin up a fine-tuning service on Isis or an external GPU node\n"
    assert corpus_hits(db, [(7, PROSE_DOC, english)], entry_values={"UP"})[0] == []
    shouted = "isis service_state is UP per the last check\n"
    assert len(corpus_hits(db, [(7, PROSE_DOC, shouted)], entry_values={"UP"})[0]) == 1
    # The fleet tiers no longer differ here: wq#1775 left them case-insensitive
    # as unmeasured, and wq#2151 measured them — 32 hits to 28, four false
    # positives of exactly this shape, none of them real.
    assert lcs.scan_text(english, index_of(db), "FLEET_MAP.md:1",
                         lcs.TIER_PROPOSAL) == []
    assert len(lcs.scan_text(shouted, index_of(db), "FLEET_MAP.md:1",
                             lcs.TIER_PROPOSAL)) == 1


def test_prose_subject_may_not_come_from_an_enclosing_heading(db):
    content = "# pbox status\n\nthe scheduler stayed active throughout\n"
    prose, _ = corpus_hits(db, [(7, PROSE_DOC, content)])
    fleet, _ = corpus_hits(db, [(7, FLEET_DOC, content)])
    assert prose == []
    assert len(fleet) == 1
    assert fleet[0]["doc_kind"] == "fleet_internal"


def test_a_hit_is_localised_to_the_chunk_that_contains_it(db):
    filler = "x" * 400 + "\n"
    content = filler + "thoth tailscale_ip 100.88.71.47 in the runbook\n"
    chunks = [(7, 0, 0, 400), (7, 1, 401, 900)]
    hits, _ = corpus_hits(db, [(7, PROSE_DOC, content)], chunks)
    assert len(hits) == 1
    assert hits[0]["chunk_ix"] == 1
    assert content[hits[0]["char_start"]:].startswith("100.88.71.47")


def test_a_hit_outside_every_chunk_span_carries_a_null_chunk(db):
    hits, _ = corpus_hits(
        db, [(7, PROSE_DOC, "thoth tailscale_ip 100.88.71.47\n")], [(7, 0, 900, 950)]
    )
    assert hits[0]["chunk_ix"] is None


def test_the_same_fact_at_the_same_offset_is_reported_once(db):
    content = ("thoth tailscale_ip 100.88.71.47 here\n"
               "thoth tailscale_ip 100.88.71.47 again\n")
    hits, _ = corpus_hits(db, [(7, PROSE_DOC, content)])
    assert len(hits) == 2
    assert len({h["char_start"] for h in hits}) == 2


def test_the_prefilter_asks_postgres_for_the_subject_too_when_scoped(db):
    index = index_of(db)
    pg = FakePG([])
    cur = pg.cursor()
    scoped = next(e for e in index if e["dead_value"] == "active")
    free = next(e for e in index if e["dead_value"] == "100.88.71.47")
    lcs.corpus_candidates(cur, scoped)
    lcs.corpus_candidates(cur, free)
    assert pg.sql[0][1] == ("%active%", "%pbox%")
    assert pg.sql[1][1] == ("%100.88.71.47%",)


def test_pgvector_unavailable_warns_and_leaves_the_sqlite_half_standing(db, monkeypatch):
    add_note(db, "n1", "nephthys is the embeddings-host for recall")

    def boom(readonly=True, env_path=None):
        raise OSError("connection refused")

    monkeypatch.setattr(lcs, "corpus_connect", boom)
    result = lcs.scan(db, artifacts=(), corpus=True)
    assert result["corpus"]["status"] == "skipped"
    assert result["actionable"] == 1
    assert "sqlite tiers above are unaffected" in lcs.render(result)
    assert lcs.main(["--db", db, "--no-files", "--corpus"]) == 0


def test_corpus_hits_never_turn_strict_red(db, monkeypatch):
    pg = FakePG([(7, PROSE_DOC, "pbox status: active today\n")])
    monkeypatch.setattr(lcs, "corpus_connect", lambda **kw: pg)
    result = lcs.scan(db, artifacts=(), corpus=True)
    assert result["actionable"] == 1
    assert result["strict_actionable"] == 0
    assert lcs.main(["--db", db, "--no-files", "--corpus", "--strict"]) == 0


def test_corpus_is_read_only_unless_corpus_apply_is_asked_for(db, monkeypatch):
    pg = FakePG([(7, PROSE_DOC, "pbox status: active today\n")])
    monkeypatch.setattr(lcs, "corpus_connect", lambda **kw: pg)
    result = lcs.scan(db, apply=True, artifacts=(), corpus=True)
    assert pg.rows == []
    assert result["corpus"]["written"] is None
    assert not any("insert into doc_contradictions" in s for s, _ in pg.sql)


def test_corpus_apply_writes_one_row_per_hit_idempotently(db, monkeypatch):
    docs = [(7, PROSE_DOC, "pbox status: active today\n")]
    pg = FakePG(docs, [(7, 3, 0, 100)])
    monkeypatch.setattr(lcs, "corpus_connect", lambda **kw: pg)
    first = lcs.scan(db, artifacts=(), corpus=True, corpus_apply=True)
    assert first["corpus"]["written"] == 1
    doc_id, chunk_ix, fact_id, subject, predicate, dead, current = pg.rows[0][:7]
    assert (doc_id, chunk_ix, subject, predicate, dead, current) == (
        7, 3, "pbox", "status", "active", "DECOMMISSIONED")

    pg.sql.clear()
    second = lcs.scan(db, artifacts=(), corpus=True, corpus_apply=True)
    assert second["corpus"]["written"] == 0
    ddl = [s for s, _ in pg.sql if s.startswith("create table")]
    assert ddl and "references research_docs(id) on delete cascade" in ddl[0]
    assert "on conflict (doc_id, fact_id, char_start) do nothing" in "\n".join(
        s for s, _ in pg.sql)


def test_corpus_apply_opens_a_writable_session_and_report_only_does_not(db, monkeypatch):
    seen = []
    monkeypatch.setattr(lcs, "corpus_connect",
                        lambda readonly=True, env_path=None: seen.append(readonly)
                        or FakePG([]))
    lcs.scan(db, artifacts=(), corpus=True)
    lcs.scan(db, artifacts=(), corpus=True, corpus_apply=True)
    assert seen == [True, False]


def test_corpus_env_is_parsed_without_leaking_it(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# comment\nLUMEN_PGVECTOR_HOST="127.0.0.1"\n\nBAD LINE\n'
                   "LUMEN_PGVECTOR_PORT=5433\n")
    parsed = lcs.corpus_env(str(env))
    assert parsed == {"LUMEN_PGVECTOR_HOST": "127.0.0.1",
                      "LUMEN_PGVECTOR_PORT": "5433"}


# --------------------------------------------------------------- ticket filing

def test_file_config_ticket_uses_the_real_wq_and_aq_argv_shapes(monkeypatch):
    """wq#1797: wq add takes --title (positional title is an argparse error)
    and aq add is positional <source> <key> <title> [msg] (bin/aq:93). The
    TIER_TICKET action must file against those shapes, not invented flags."""
    calls = []
    monkeypatch.setattr(lcs, "run", lambda cmd, timeout=120: calls.append(cmd) or (0, ""))
    hits = [{
        "location": "storage_manifest.yaml:1",
        "subject": "hathor", "predicate": "power_plug",
        "dead_value": "P3", "valid_to": "2026-07-01", "current_value": "P5",
    }]

    lcs.file_config_ticket(hits)

    assert len(calls) == 2
    wq_cmd, aq_cmd = calls

    assert wq_cmd[:2] == [lcs.WQ, "add"]
    assert "--title" in wq_cmd
    title = wq_cmd[wq_cmd.index("--title") + 1]
    assert title == "[minerva] stale superseded values in storage_manifest.yaml"

    assert aq_cmd[0] == lcs.AQ
    assert aq_cmd[1] == "add"
    assert aq_cmd[2] == "lumen-contradiction-scan"
    assert aq_cmd[3] == "storage_manifest.yaml"
    assert aq_cmd[4] == title
