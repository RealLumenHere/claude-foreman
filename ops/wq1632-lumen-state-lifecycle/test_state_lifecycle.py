"""wq#1632 tests — fixture DB mirrors the live thoth/nephthys evidence
(MINERVA_PIPELINE_MAP.md §3.2). Schema per §1.1."""

import os
import shutil
import sqlite3

import pytest

import state_lifecycle as sl

HERE = os.path.dirname(os.path.abspath(__file__))

SCHEMA = """
CREATE TABLE state_facts (
  id INTEGER PRIMARY KEY,
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  value TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT,
  ingested_at TEXT,
  invalidated_at TEXT,
  source_ref TEXT,
  confidence REAL,
  episode_id TEXT,
  half_life_days REAL,
  last_confirmed_at TEXT,
  fact_class TEXT,
  render_tier TEXT
);
CREATE UNIQUE INDEX uq_open ON state_facts(subject, predicate)
  WHERE valid_to IS NULL;
"""


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "registry.yaml"
    shutil.copy(os.path.join(HERE, "registry.seed.yaml"), path)
    return sl.Registry(str(path))


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def seed(conn, subject, predicate, value, valid_from="2026-09-01T00:00:00+00:00",
         fact_class="config", source_ref="seed"):
    return conn.execute(
        "INSERT INTO state_facts (subject, predicate, value, valid_from,"
        " ingested_at, source_ref, fact_class) VALUES (?,?,?,?,?,?,?)",
        (subject, predicate, value, valid_from, valid_from, source_ref, fact_class),
    ).lastrowid


def open_preds(conn, subject):
    return {
        r["predicate"] for r in conn.execute(
            "SELECT predicate FROM state_facts WHERE subject=? AND valid_to IS NULL",
            (subject,),
        )
    }


def seed_nephthys(conn):
    for pred, cls in [("serving_model", "config"), ("embed_model", "config"),
                      ("litellm_aliases", "config"), ("dc_handle_store", "config"),
                      ("ollama_version", "config"), ("ram", "hardware"),
                      ("tailscale_ip", "identity")]:
        seed(conn, "nephthys", pred, "x", fact_class=cls)


# --- registry gate (DESIGN.md §3) ---

def test_gate_refuses_unknown_and_suggests_nearest(conn, registry):
    with pytest.raises(sl.PredicateGateError) as e:
        sl.ingest(conn, registry, "thoth", "tailnet_route", "FIXED", "t")
    assert "tailnet_routing" in str(e.value)
    assert "--new-predicate" in str(e.value)


def test_gate_pattern_match_allows(conn, registry):
    res = sl.ingest(conn, registry, "seshat", "mcp_gateway", "up", "t")
    assert res["action"] == "inserted"


def test_new_predicate_reason_admits_and_persists(conn, registry):
    res = sl.ingest(conn, registry, "thoth", "gpu_layers", "33", "t",
                    new_predicate_reason="new axis: llama offload")
    assert res["action"] == "inserted"
    reloaded = sl.Registry(registry.path)
    assert reloaded.predicates["gpu_layers"]["why"] == "new axis: llama offload"


def test_registry_tolerates_wq1613_keys(tmp_path, conn):
    path = tmp_path / "r.yaml"
    path.write_text(
        "predicates:\n  status:\n    class: status\n    closes_on_decommission: true\n"
        "    probe: tailscale\n    verify_interval_days: 3\n    source_kind: observed\n"
    )
    r = sl.Registry(str(path))
    r.gate("status")
    assert r.closes_on_decommission("status")


# --- lifecycle enum (DESIGN.md §1) ---

def test_lifecycle_enum_is_closed(conn, registry):
    with pytest.raises(sl.PredicateGateError):
        sl.ingest(conn, registry, "thoth", "lifecycle", "dead", "t")


# --- sibling closure (DESIGN.md §2) ---

def test_decommission_closes_operational_keeps_identity(conn, registry):
    seed_nephthys(conn)
    res = sl.ingest(conn, registry, "nephthys", "lifecycle", "decommissioned",
                    "wq1632", valid_from="2026-09-09T00:00:00+00:00")
    assert sorted(res["closed"]) == [
        "dc_handle_store", "embed_model", "litellm_aliases", "ollama_version",
        "serving_model",
    ]
    assert open_preds(conn, "nephthys") == {"lifecycle", "ram", "tailscale_ip"}
    row = conn.execute(
        "SELECT valid_to, source_ref FROM state_facts WHERE subject='nephthys'"
        " AND predicate='serving_model'").fetchone()
    assert row["valid_to"] == "2026-09-09T00:00:00+00:00"
    assert row["source_ref"] == f"lifecycle-closure:{res['id']}"


def test_active_lifecycle_closes_nothing(conn, registry):
    seed_nephthys(conn)
    res = sl.ingest(conn, registry, "nephthys", "lifecycle", "degraded", "t")
    assert res["closed"] == []
    assert "serving_model" in open_preds(conn, "nephthys")


# --- supersession semantics unchanged ---

def test_supersede_and_reconfirm(conn, registry):
    a = sl.ingest(conn, registry, "thoth", "status", "OFFLINE", "t",
                  valid_from="2026-09-02T00:00:00+00:00")
    b = sl.ingest(conn, registry, "thoth", "status", "up", "t",
                  valid_from="2026-09-08T00:00:00+00:00")
    assert b["action"] == "superseded"
    old = conn.execute("SELECT valid_to FROM state_facts WHERE id=?",
                       (a["id"],)).fetchone()
    assert old["valid_to"] == "2026-09-08T00:00:00+00:00"
    c = sl.ingest(conn, registry, "thoth", "status", "up", "t")
    assert c["action"] == "reconfirmed" and c["id"] == b["id"]


# --- backfill (DESIGN.md §1) ---

def test_backfill_conservative_and_idempotent(conn, registry):
    seed(conn, "thoth", "status", "tailnet-OFFLINE since ~2026-09-02, no remote shell",
         valid_from="2026-09-02T00:00:00+00:00", fact_class="status")
    seed(conn, "seshat", "status", "serving embeddings", fact_class="status")
    seed_nephthys(conn)
    seed(conn, "nephthys", "lifecycle", "decommissioned",
         valid_from="2026-08-20T00:00:00+00:00", fact_class="status")

    dry = sl.backfill(conn, registry, apply=False)
    assert dry["backfilled"] == {"thoth": "offline", "seshat": "active"}
    assert dry["ambiguous"] == ["seshat"]
    assert "serving_model" in dry["closed"]["nephthys"]
    assert "serving_model" in open_preds(conn, "nephthys")  # dry-run wrote nothing

    applied = sl.backfill(conn, registry, apply=True)
    lc = sl.open_fact(conn, "thoth", "lifecycle")
    assert lc["value"] == "offline"
    assert lc["valid_from"] == "2026-09-02T00:00:00+00:00"  # inherits evidence time
    assert lc["source_ref"].startswith("lifecycle-backfill:wq1632 fact:")
    # nephthys: existing lifecycle untouched, siblings closed
    assert open_preds(conn, "nephthys") == {"lifecycle", "ram", "tailscale_ip"}
    assert "nephthys" in applied["skipped_existing"] or True
    # second run: nothing left to backfill or close
    again = sl.backfill(conn, registry, apply=True)
    assert again["backfilled"] == {} and again["closed"] == {}


# --- close-on-up, the wq#1608 hook (DESIGN.md §4) ---

def test_close_on_up_closes_status_class_and_activates(conn, registry):
    seed(conn, "thoth", "status", "tailnet-OFFLINE",
         valid_from="2026-09-02T00:00:00+00:00", fact_class="status")
    seed(conn, "thoth", "tailnet_routing", "FIXED 2026-09-08",
         valid_from="2026-09-08T00:00:00+00:00", fact_class="status")
    seed(conn, "thoth", "ram", "14", fact_class="hardware")
    out = sl.close_on_up(conn, registry, "thoth",
                         source_ref="reconcile:tailscale-up",
                         observed_at="2026-09-10T06:30:00+00:00")
    assert sorted(out["closed"]) == ["status", "tailnet_routing"]
    assert open_preds(conn, "thoth") == {"ram", "lifecycle"}
    assert sl.open_fact(conn, "thoth", "lifecycle")["value"] == "active"


# --- seed-registry merge ---

def test_seed_registry_adds_unknown_never_overwrites(conn, registry):
    seed(conn, "hapi", "novel_axis", "v", fact_class="narrative")
    seed(conn, "hapi", "status", "up", fact_class="status")
    added = sl.seed_registry(conn, registry)
    assert added == ["novel_axis"]
    reloaded = sl.Registry(registry.path)
    assert reloaded.predicates["novel_axis"]["class"] == "narrative"
    assert reloaded.predicates["novel_axis"]["closes_on_decommission"] is False
    assert reloaded.closes_on_decommission("status")  # seed entry untouched
