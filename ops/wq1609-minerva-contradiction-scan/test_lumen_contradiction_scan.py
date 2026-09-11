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
    assert result["closed_facts"] == 7
    assert result["index_size"] == 5
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
    assert '"index_size": 5' in payload
    assert lcs.main(["--db", db, "--strict", "--no-files"]) == 1
    assert lcs.main(["--db", "/nonexistent/minerva.db", "--no-files"]) == 2
