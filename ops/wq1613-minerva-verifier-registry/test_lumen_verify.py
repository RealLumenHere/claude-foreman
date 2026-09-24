"""wq#1613 tests — verifier registry + lumen_verify sweep.

wq#2053: reconcile() now delegates to lumen_state.report_observation(), so the
fixture DB is the store's REAL schema (lumen_state.init_schema) in a throwaway
file, and lumen_state._now is pinned to NOW so timestamps stay deterministic.
lumen_verify.migrate() still adds its columns on top, additive and idempotent
(MINERVA_STALENESS_REVIEW.md section 3.1).
"""
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import lumen_verify as lv

sys.path.insert(0, lv.MINERVA_DIR)
import lumen_state as ls  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

NOW = datetime(2026, 9, 10, 6, 0, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "DB", str(tmp_path / "test.db"))
    monkeypatch.setattr(ls, "_now", lambda: NOW.isoformat())
    ls.init_schema()
    c = sqlite3.connect(ls.DB)
    c.row_factory = sqlite3.Row
    ls._migrate(c)
    yield c
    c.close()


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "registry.yaml"
    import shutil
    shutil.copy(os.path.join(HERE, "registry.yaml"), path)
    return lv.Registry(str(path))


def seed(conn, subject, predicate, value, age_days, fact_class="identity",
         source_kind=None, verify_interval_days=None):
    ts = iso(NOW - timedelta(days=age_days))
    fid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO state_facts (id, subject, predicate, value, valid_from, "
        "ingested_at, last_confirmed_at, fact_class, source_ref) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (fid, subject, predicate, value, ts, ts, ts, fact_class, "seed"),
    )
    conn.commit()
    lv.migrate(conn)
    if source_kind:
        conn.execute("UPDATE state_facts SET source_kind=? WHERE id=?", (source_kind, fid))
    if verify_interval_days is not None:
        conn.execute("UPDATE state_facts SET verify_interval_days=? WHERE id=?",
                     (verify_interval_days, fid))
    conn.commit()
    return fid


# --------------------------------------------------------------- migration

def test_migrate_adds_columns_idempotently(conn):
    lv.migrate(conn)
    lv.migrate(conn)  # must not raise on the second pass
    cols = {r[1] for r in conn.execute("PRAGMA table_info(state_facts)")}
    assert {"source_kind", "verify_recipe", "verify_interval_days",
            "contradicted_at", "derived_from"} <= cols
    conn.execute("SELECT * FROM fact_verifications")  # table exists


# ------------------------------------------------------------------ due_facts

def test_due_by_class_default(conn, registry):
    # identity default is 90d; 91 days old is due, 10 days old is not.
    seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=91, fact_class="identity")
    seed(conn, "hathor", "role", "worker", age_days=10, fact_class="identity")
    batches = lv.due_facts(conn, registry, NOW)
    due_preds = {f["predicate"] for facts in batches.values() for f in facts}
    assert "tailscale_ip" in due_preds
    assert "role" not in due_preds  # 'role' has no probe assigned at all


def test_due_skips_predicate_with_no_probe(conn, registry):
    seed(conn, "hathor", "hardware", "mac-mini-m2", age_days=999, fact_class="hardware")
    batches = lv.due_facts(conn, registry, NOW)
    assert not batches


def test_fact_level_interval_overrides_registry(conn, registry):
    # tailscale_ip normally 1d; a fact-level override of 30d should suppress it at 5d old.
    seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5, fact_class="identity",
         verify_interval_days=30)
    batches = lv.due_facts(conn, registry, NOW)
    assert not batches


def test_batches_grouped_per_probe_and_subject(conn, registry):
    seed(conn, "hathor", "lan_ip", "192.168.1.5", age_days=2, fact_class="identity")
    seed(conn, "hathor", "ssh_user", "lumen", age_days=2, fact_class="identity")
    seed(conn, "thoth", "lan_ip", "192.168.1.6", age_days=2, fact_class="identity")
    batches = lv.due_facts(conn, registry, NOW)
    assert set(batches) == {("fleet_inventory", "hathor"), ("fleet_inventory", "thoth")}
    assert len(batches[("fleet_inventory", "hathor")]) == 2


# ------------------------------------------------------------------ reconcile

def get_fact(conn, fid):
    return dict(conn.execute("SELECT * FROM state_facts WHERE id=?", (fid,)).fetchone())


def test_reconcile_confirms_on_match(conn, registry):
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5)
    fact = get_fact(conn, fid)
    outcome = lv.reconcile(conn, fact, "tailscale_ip", "100.1.1.1", None, NOW)
    assert outcome == "confirmed"
    row = get_fact(conn, fid)
    assert row["last_confirmed_at"] == iso(NOW)
    assert row["verify_recipe"] == "tailscale_ip"  # wq#2150: shows as verified, not UNVERIFIABLE
    assert row["valid_to"] is None
    verif = conn.execute("SELECT outcome FROM fact_verifications WHERE fact_id=?",
                          (fid,)).fetchone()
    assert verif["outcome"] == "confirmed"


def test_reconcile_supersedes_on_mismatch(conn, registry):
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5,
               source_kind="observed")
    fact = get_fact(conn, fid)
    outcome = lv.reconcile(conn, fact, "tailscale_ip", "100.1.1.2", None, NOW)
    assert outcome == "superseded"
    old = get_fact(conn, fid)
    assert old["valid_to"] == iso(NOW)
    new = conn.execute(
        "SELECT * FROM state_facts WHERE subject='hathor' AND predicate='tailscale_ip' "
        "AND valid_to IS NULL"
    ).fetchone()
    assert dict(new)["value"] == "100.1.1.2"
    assert dict(new)["source_kind"] == "observed"


def test_reconcile_error_never_mutates(conn, registry):
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5)
    fact = get_fact(conn, fid)
    outcome = lv.reconcile(conn, fact, "tailscale_ip", None, "tailscale rc=1", NOW)
    assert outcome == "unknown"
    row = get_fact(conn, fid)
    assert row["valid_to"] is None
    assert row["value"] == "100.1.1.1"
    assert row["last_confirmed_at"] != iso(NOW)


def test_reconcile_frank_stated_never_superseded(conn, registry, monkeypatch):
    filed = []
    monkeypatch.setattr(lv, "file_conflict",
                         lambda *a, **k: filed.append(a))
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5,
               source_kind="frank_stated")
    fact = get_fact(conn, fid)
    outcome = lv.reconcile(conn, fact, "tailscale_ip", "100.1.1.99", None, NOW)
    assert outcome == "unknown"
    row = get_fact(conn, fid)
    assert row["valid_to"] is None
    assert row["value"] == "100.1.1.1"  # never overwritten
    assert filed and filed[0][0] == "hathor"
    verif = conn.execute("SELECT outcome, observed FROM fact_verifications WHERE fact_id=?",
                          (fid,)).fetchone()
    assert verif["outcome"] == "unknown"
    assert verif["observed"] == "100.1.1.99"  # conflict is on record, just not applied


# ---------------------------------------------------------------------- sweep

def test_sweep_probe_exception_is_fail_open(conn, registry, monkeypatch):
    def boom(subject, facts):
        raise RuntimeError("ssh timed out")
    monkeypatch.setitem(lv.PROBES, "tailscale_ip", boom)
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5)
    result = lv.sweep(conn, registry, now=NOW)
    assert result["counts"]["unknown"] == 1
    assert get_fact(conn, fid)["valid_to"] is None


def test_sweep_dry_run_does_not_write(conn, registry, monkeypatch):
    monkeypatch.setitem(lv.PROBES, "tailscale_ip",
                         lambda subject, facts: {(subject, f["predicate"]): (f["value"], None)
                                                  for f in facts})
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5)
    result = lv.sweep(conn, registry, now=NOW, dry_run=True)
    assert result["counts"]["confirmed"] == 1
    assert conn.execute("SELECT count(*) FROM fact_verifications").fetchone()[0] == 0
    assert get_fact(conn, fid)["last_confirmed_at"] != iso(NOW)


# ------------------------------------------------- wq#2192 no-clobber guards

def no_run(argv, timeout=20):
    raise AssertionError(f"probe must not run {argv}")


@pytest.mark.parametrize("predicate", ["ollama_version", "netdata_version", "os"])
def test_os_kernel_probe_observes_nothing_but_kernel(monkeypatch, predicate):
    # `uname -s` used to answer these with "Linux" and overwrite the real value.
    monkeypatch.setattr(lv, "run", no_run)
    facts = [{"predicate": predicate, "value": "0.32.6"}]
    observed, error = lv.probe_os_kernel("isis", facts)[("isis", predicate)]
    assert observed is None and error


def test_os_kernel_probe_still_reads_kernel(monkeypatch):
    monkeypatch.setattr(lv, "run", lambda argv, timeout=20: (0, "6.8.0-1\n", ""))
    facts = [{"predicate": "kernel", "value": "6.8.0-0"}]
    assert lv.probe_os_kernel("lumen", facts)[("lumen", "kernel")] == ("6.8.0-1", None)


def test_registry_assigns_no_probe_to_unobservable_predicates(registry):
    for predicate in ("ollama_version", "netdata_version", "os"):
        assert registry.probe_id(predicate) is None
    assert registry.probe_id("kernel") == "os_kernel"


def test_service_state_probe_ignores_free_text_status(monkeypatch):
    # `systemctl is-active RESTORED` prints "inactive"; that used to become the fact.
    monkeypatch.setattr(lv, "run", no_run)
    facts = [{"predicate": "status", "value": "RESTORED"}]
    observed, error = lv.probe_service_state("bes", facts)[("bes", "status")]
    assert observed is None and error


def test_service_state_probe_unit_name_only_confirms(monkeypatch):
    monkeypatch.setattr(lv, "run", lambda argv, timeout=20: (0, "active\n", ""))
    facts = [{"predicate": "nginx_status", "value": "nginx.service"}]
    assert lv.probe_service_state("lumen", facts)[("lumen", "nginx_status")] == (
        "nginx.service", None)
    monkeypatch.setattr(lv, "run", lambda argv, timeout=20: (3, "inactive\n", ""))
    observed, error = lv.probe_service_state("lumen", facts)[("lumen", "nginx_status")]
    assert observed is None and "inactive" in error


def test_sweep_leaves_status_and_version_facts_untouched(conn, registry, monkeypatch):
    monkeypatch.setattr(lv, "run", lambda argv, timeout=20: (3, "inactive\n", ""))
    status = seed(conn, "bes", "status", "RESTORED", age_days=5, fact_class="status")
    version = seed(conn, "isis", "ollama_version", "0.32.6", age_days=30, fact_class="config")
    os_fact = seed(conn, "bes", "os", "Ubuntu Server 26.04", age_days=30, fact_class="config")
    result = lv.sweep(conn, registry, now=NOW)
    assert result["counts"]["superseded"] == 0
    for fid, value in ((status, "RESTORED"), (version, "0.32.6"),
                       (os_fact, "Ubuntu Server 26.04")):
        row = get_fact(conn, fid)
        assert row["valid_to"] is None and row["value"] == value
    assert conn.execute("SELECT count(*) FROM state_facts").fetchone()[0] == 3


def test_reconcile_blank_observation_is_unknown(conn, registry):
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5,
               source_kind="observed")
    assert lv.reconcile(conn, get_fact(conn, fid), "tailscale_ip", "  ", None, NOW) == "unknown"
    assert get_fact(conn, fid)["valid_to"] is None


def test_reconcile_supersede_keeps_store_invariants(conn, registry):
    """wq#2053: the supersede runs through ingest(), so it leaves the memory_audit
    trail the old raw INSERT skipped, and the new row stays verifiable."""
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5,
               source_kind="observed")
    assert lv.reconcile(conn, get_fact(conn, fid), "tailscale_ip", "100.1.1.2",
                        None, NOW) == "superseded"
    new = dict(conn.execute(
        "SELECT * FROM state_facts WHERE subject='hathor' AND predicate='tailscale_ip' "
        "AND valid_to IS NULL").fetchone())
    assert new["verify_recipe"] == "tailscale_ip"
    assert get_fact(conn, fid)["closed_by"] == new["id"]
    audited = {r["action"] for r in conn.execute(
        "SELECT action FROM memory_audit WHERE row_id=?", (new["id"],))}
    assert "supersede" in audited
    verif = conn.execute("SELECT outcome FROM fact_verifications WHERE fact_id=?",
                         (fid,)).fetchone()
    assert verif["outcome"] == "superseded"


def test_reconcile_cannot_reopen_a_lifecycle_closed_node(conn, registry):
    """wq#2053: the gap the ticket names. A stale sweep observing a value for a
    decommissioned node's operational fact used to INSERT it as the open row."""
    reg = ls.registry()
    ls.ingest("khepri", reg["lifecycle"], reg["closing"][0])
    assert ls.is_operational("caddy_status", reg)  # the gate only guards operational facts
    fid = seed(conn, "khepri", "caddy_status", "active", age_days=5, fact_class="status",
               source_kind="observed")  # legacy open row under a closed lifecycle
    outcome = lv.reconcile(conn, get_fact(conn, fid), "service_state", "inactive",
                           None, NOW)
    assert outcome == "unknown"
    open_rows = conn.execute(
        "SELECT value FROM state_facts WHERE subject='khepri' AND predicate='caddy_status' "
        "AND valid_to IS NULL").fetchall()
    assert [r["value"] for r in open_rows] == ["active"]  # untouched, nothing new open
    verif = conn.execute("SELECT outcome, detail FROM fact_verifications WHERE fact_id=?",
                         (fid,)).fetchone()
    assert verif["outcome"] == "unknown" and "LifecycleClosedError" in verif["detail"]


def test_reconcile_error_still_logs_an_unknown_verification(conn, registry):
    fid = seed(conn, "hathor", "tailscale_ip", "100.1.1.1", age_days=5)
    lv.reconcile(conn, get_fact(conn, fid), "tailscale_ip", None, "tailscale rc=1", NOW)
    verif = conn.execute("SELECT outcome, detail FROM fact_verifications WHERE fact_id=?",
                         (fid,)).fetchone()
    assert verif["outcome"] == "unknown" and verif["detail"] == "tailscale rc=1"


# --------------------------------------------------------------- node_liveness

def test_probe_node_liveness_unreachable_is_offline_not_error(monkeypatch):
    # wq#1620: ssh AND tailscale ping both failing (khepri/wadjet's actual
    # state) must be reported as the observation "offline", not an unknown
    # probe error -- otherwise a genuinely offline node's stale POWERED ON /
    # available fact can never be superseded by the sweep.
    monkeypatch.setattr(lv, "run", lambda argv, timeout=20: (255, "", "unreachable"))
    facts = [{"predicate": "availability", "value": "available"}]
    result = lv.probe_node_liveness("wadjet", facts)
    assert result[("wadjet", "availability")] == ("offline", None)


def test_probe_node_liveness_ssh_ok_is_available(monkeypatch):
    def fake_run(argv, timeout=20):
        return (0, "", "") if argv[0] == "ssh" else (1, "", "no route")
    monkeypatch.setattr(lv, "run", fake_run)
    facts = [{"predicate": "availability", "value": "offline"}]
    result = lv.probe_node_liveness("khepri", facts)
    assert result[("khepri", "availability")] == ("available", None)


def test_sweep_supersedes_stale_availability_when_node_goes_offline(conn, registry, monkeypatch):
    monkeypatch.setattr(lv, "run", lambda argv, timeout=20: (255, "", "unreachable"))
    fid = seed(conn, "wadjet", "availability", "available", age_days=5)
    result = lv.sweep(conn, registry, now=NOW)
    assert result["counts"]["superseded"] == 1
    assert get_fact(conn, fid)["valid_to"] == iso(NOW)


# ------------------------------------------------ wq#2150 fleet_inventory probe

IP_OUT = ("1: lo    inet 127.0.0.1/8 scope host lo\n"
          "2: eno1    inet 192.168.8.218/24 brd 192.168.8.255 scope global eno1\n"
          "3: tailscale0    inet 100.75.1.108/32 scope global tailscale0\n")


def test_fleet_inventory_never_reads_the_store_through_fleet_inv(monkeypatch):
    """fleet-inv reads lan_ip/ssh.user from state_facts, so asking it would confirm
    the store with itself. The probe must observe the node instead."""
    seen = []

    def fake_run(argv, timeout=20):
        seen.append(argv)
        return 0, IP_OUT, ""
    monkeypatch.setattr(lv, "run", fake_run)
    lv.probe_fleet_inventory("thoth", [{"predicate": "lan_ip", "value": "192.168.8.218"}])
    assert seen and all(a[0] != "fleet-inv" for a in seen)
    assert seen[0][0] == "ssh" and "addr" in seen[0]


def test_fleet_inventory_lan_ip_confirms_only_an_address_the_node_has(monkeypatch):
    monkeypatch.setattr(lv, "run", lambda argv, timeout=20: (0, IP_OUT, ""))
    facts = [{"predicate": "lan_ip", "value": "192.168.8.218"}]
    assert lv.probe_fleet_inventory("thoth", facts)[("thoth", "lan_ip")] == (
        "192.168.8.218", None)
    facts = [{"predicate": "lan_ip", "value": "192.168.8.99"}]
    observed, error = lv.probe_fleet_inventory("thoth", facts)[("thoth", "lan_ip")]
    assert observed is None and "not among" in error  # unknown, never a new value


def test_fleet_inventory_lan_ip_ssh_failure_is_unknown(monkeypatch):
    monkeypatch.setattr(lv, "run", lambda argv, timeout=20: (255, "", "timed out"))
    observed, error = lv.probe_fleet_inventory(
        "thoth", [{"predicate": "lan_ip", "value": "192.168.8.218"}])[("thoth", "lan_ip")]
    assert observed is None and "timed out" in error


def test_fleet_inventory_lan_ip_local_subject_runs_locally(monkeypatch):
    seen = []
    monkeypatch.setattr(lv, "run", lambda argv, timeout=20: (seen.append(argv), (0, IP_OUT, ""))[1])
    lv.probe_fleet_inventory("lumen", [{"predicate": "lan_ip", "value": "192.168.8.218"}])
    assert seen[0][0] == "ip"


def test_fleet_inventory_ssh_user_confirms_on_login_and_rejects_option_injection(monkeypatch):
    seen = []
    monkeypatch.setattr(lv, "run", lambda argv, timeout=20: (seen.append(argv), (0, "", ""))[1])
    ok = lv.probe_fleet_inventory("thoth", [{"predicate": "ssh_user", "value": "thoth"}])
    assert ok[("thoth", "ssh_user")] == ("thoth", None)
    assert seen[0][seen[0].index("-l") + 1] == "thoth"
    seen.clear()
    bad = lv.probe_fleet_inventory("thoth", [{"predicate": "ssh_user", "value": "-oProxyCommand=x"}])
    assert bad[("thoth", "ssh_user")][0] is None and not seen  # never reaches ssh


def test_fleet_inventory_ssh_user_login_failure_is_unknown(monkeypatch):
    monkeypatch.setattr(lv, "run", lambda argv, timeout=20: (255, "", "Permission denied"))
    observed, error = lv.probe_fleet_inventory(
        "thoth", [{"predicate": "ssh_user", "value": "thoth"}])[("thoth", "ssh_user")]
    assert observed is None and "Permission denied" in error


def test_power_plug_has_no_probe(registry):
    assert registry.probe_id("power_plug") is None
    assert registry.probe_id("lan_ip") == "fleet_inventory"


def test_sweep_skips_ssh_probes_for_closed_lifecycle_subjects(conn, registry, monkeypatch):
    reg = ls.registry()
    ls.ingest("wadjet", reg["lifecycle"], reg["closing"][-1])
    ssh_calls = []
    monkeypatch.setattr(lv, "run", lambda argv, timeout=20: (ssh_calls.append(argv),
                                                            (255, "", "timeout"))[1])
    fid = seed(conn, "wadjet", "lan_ip", "192.168.8.5", age_days=5)
    alive = seed(conn, "thoth", "lan_ip", "192.168.8.218", age_days=5)
    monkeypatch.setitem(lv.PROBES, "fleet_inventory",
                        lambda subject, facts: {(subject, f["predicate"]): (f["value"], None)
                                                for f in facts})
    result = lv.sweep(conn, registry, now=NOW)
    assert result["counts"]["skipped"] == 1 and result["counts"]["confirmed"] == 1
    assert not ssh_calls
    assert conn.execute("SELECT count(*) FROM fact_verifications WHERE fact_id=?",
                        (fid,)).fetchone()[0] == 0  # nothing was checked, nothing logged
    assert get_fact(conn, alive)["last_confirmed_at"] == iso(NOW)


# ------------------------------------- wq#2204 endpoint_live / node_storage

class FakeResp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def stub_urlopen(monkeypatch, status=200, exc=None):
    import urllib.request

    def fake(url, timeout=None):
        if exc:
            raise exc
        return FakeResp(status)
    monkeypatch.setattr(urllib.request, "urlopen", fake)


def test_endpoint_probe_confirms_with_fact_value_not_status(monkeypatch):
    # The HTTP status ('200') used to be the observation and replaced url/port.
    stub_urlopen(monkeypatch)
    facts = [{"predicate": "port", "value": "11434"},
             {"predicate": "url", "value": "http://ollama:11434/api"}]
    result = lv.probe_endpoint_live("ollama", facts)
    assert result[("ollama", "port")] == ("11434", None)
    assert result[("ollama", "url")] == ("http://ollama:11434/api", None)


def test_endpoint_probe_bad_status_or_failure_is_unknown(monkeypatch):
    facts = [{"predicate": "port", "value": "11434"}]
    stub_urlopen(monkeypatch, status=500)
    observed, error = lv.probe_endpoint_live("ollama", facts)[("ollama", "port")]
    assert observed is None and "500" in error
    stub_urlopen(monkeypatch, exc=OSError("refused"))
    observed, error = lv.probe_endpoint_live("ollama", facts)[("ollama", "port")]
    assert observed is None and "refused" in error


@pytest.mark.parametrize("predicate,value", [("url", "ollama on isis"), ("url", "file:///etc/passwd"),
                                             ("port", "11434/tcp")])
def test_endpoint_probe_skips_values_that_are_not_addresses(monkeypatch, predicate, value):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", no_run)
    observed, error = lv.probe_endpoint_live("x", [{"predicate": predicate, "value": value}])[("x", predicate)]
    assert observed is None and error


def dimm(*sizes):
    return "".join(f"Memory Device\n\tSize: {s}\n\tLocator: DIMM\n" for s in sizes) + \
        "Memory Device\n\tSize: No Module Installed\n"


def lsblk(*disks):
    import json
    return json.dumps({"blockdevices": [{"name": n, "size": s, "type": t} for n, s, t in disks]})


def run_returning(out):
    return lambda argv, timeout=20: (0, out, "")


@pytest.mark.parametrize("stored", ["64GB", "64 GB", "64GB DDR5 (4x16GB)"])
def test_ram_probe_sums_all_dimms_and_confirms_stated_total(monkeypatch, stored):
    monkeypatch.setattr(lv, "run", run_returning(dimm("16 GB", "16 GB", "16 GB", "16 GB")))
    assert lv.probe_node_storage("lumen", [{"predicate": "ram", "value": stored}])[
        ("lumen", "ram")] == (stored, None)


def test_ram_probe_first_dimm_size_is_not_the_observation(monkeypatch):
    monkeypatch.setattr(lv, "run", run_returning(dimm("16 GB", "16 GB", "16 GB", "16 GB")))
    observed, error = lv.probe_node_storage("lumen", [{"predicate": "ram", "value": "128GB"}])[
        ("lumen", "ram")]
    assert observed is None and error


def test_storage_probe_confirms_matching_size_only(monkeypatch):
    disks = [("nvme0n1", 1000204886016, "disk"), ("nvme1n1", 1000204886016, "disk"),
             ("loop0", 4096, "loop"), ("sr0", 1073741312, "rom")]
    monkeypatch.setattr(lv, "run", run_returning(lsblk(*disks)))
    facts = [{"predicate": "storage", "value": "2x 1TB NVMe RAID1"}]
    assert lv.probe_node_storage("lumen", facts)[("lumen", "storage")] == (
        "2x 1TB NVMe RAID1", None)
    facts = [{"predicate": "storage", "value": "4TB spinning rust"}]
    observed, error = lv.probe_node_storage("lumen", facts)[("lumen", "storage")]
    assert observed is None and error
    facts = [{"predicate": "storage", "value": "RAID1 mirror"}]
    assert lv.probe_node_storage("lumen", facts)[("lumen", "storage")][0] is None


def test_storage_probe_never_returns_a_byte_sum(monkeypatch):
    monkeypatch.setattr(lv, "run", run_returning(lsblk(("sda", 500107862016, "disk"))))
    for stored in ("500GB", "some free text", "1TB"):
        observed, _ = lv.probe_node_storage("lumen", [{"predicate": "storage", "value": stored}])[
            ("lumen", "storage")]
        assert observed in (None, stored)


def test_node_storage_probe_unparseable_and_unobservable_are_unknown(monkeypatch):
    monkeypatch.setattr(lv, "run", run_returning("not json"))
    assert lv.probe_node_storage("lumen", [{"predicate": "storage", "value": "1TB"}])[
        ("lumen", "storage")][0] is None
    monkeypatch.setattr(lv, "run", no_run)
    assert lv.probe_node_storage("lumen", [{"predicate": "storage_layout", "value": "x"}])[
        ("lumen", "storage_layout")][0] is None


def test_sweep_leaves_url_port_ram_storage_untouched(conn, registry, monkeypatch):
    stub_urlopen(monkeypatch, status=200)
    monkeypatch.setattr(lv, "run", run_returning(lsblk(("sda", 500107862016, "disk"))))
    ids = {
        ("ollama", "port"): seed(conn, "ollama", "port", "11434", age_days=5, fact_class="config"),
        ("ollama", "url"): seed(conn, "ollama", "url", "http://ollama:11434/", age_days=5,
                                fact_class="config"),
        ("isis", "storage"): seed(conn, "isis", "storage", "2TB free text", age_days=30,
                                  fact_class="hardware"),
        ("isis", "ram"): seed(conn, "isis", "ram", "128GB", age_days=30, fact_class="hardware"),
    }
    result = lv.sweep(conn, registry, now=NOW)
    assert result["counts"]["superseded"] == 0
    for fid in ids.values():
        assert get_fact(conn, fid)["valid_to"] is None
    assert conn.execute("SELECT count(*) FROM state_facts").fetchone()[0] == len(ids)
