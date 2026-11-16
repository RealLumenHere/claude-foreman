"""wq#5008 — QWED dual-axis taxonomy over lumen_state facts (harness/fact_taxonomy.py).
Runs against a synthetic state DB; the real minerva.db is never opened."""
import itertools
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fact_taxonomy as ft

NOW = 1_800_000_000.0
needs_z3 = pytest.mark.skipif(ft._load("z3") is None, reason="z3-solver not installed")
needs_sqlglot = pytest.mark.skipif(ft._load("sqlglot") is None, reason="sqlglot not installed")
needs_sympy = pytest.mark.skipif(ft._load("sympy") is None, reason="sympy not installed")


def iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def fact(pred, value, subject="node", fid=None, **kw):
    f = {"id": fid or f"{subject}-{pred}", "subject": subject, "predicate": pred,
         "value": value, "valid_from": iso(NOW - 86400), "last_confirmed_at": None,
         "fact_class": "identity", "source_kind": None, "verify_recipe": None,
         "verify_interval_days": None, "contradicted_at": None}
    f.update(kw)
    return f


def classify(f, ver=None, **kw):
    return ft.classify_fact(f, ver, NOW, **kw)


# ------------------------------------------------------------------ math (SymPy)

@needs_sympy
@pytest.mark.parametrize("value,verdict", [
    ("64GB = 4x16GB DDR3-1866 ECC RDIMM", ft.PROVEN_TRUE),
    ("16GB = 8x2GB LPDDR4-4267 soldered", ft.PROVEN_TRUE),
    ("32GB DDR5-4800 (2x16GB) — second stick purchased", ft.PROVEN_TRUE),
    ("16GB = 1x16GB DDR4-3200 SODIMM, 2nd slot empty", ft.PROVEN_TRUE),
    ("7.5GB = 3x2.5GB", ft.PROVEN_TRUE),
    ("32GB = 2x8GB DDR4", ft.PROVEN_FALSE),
    ("64GB (4x8GB)", ft.PROVEN_FALSE),
])
def test_math_identities(value, verdict):
    checks = ft.check_math(value)
    assert [c["verdict"] for c in checks] == [verdict]
    assert checks[0]["engine"] == "sympy"


@pytest.mark.parametrize("value", [
    "64GB DDR4", "3.8GiB — (zram swap 1.9G)", "1TB = 2x512GB", "i7 4C/8T", "", "2x16GB",
])
def test_math_reads_no_claim_from_prose_or_mixed_units(value):
    assert ft.check_math(value) == []


@needs_sympy
def test_proven_false_is_verified_and_denied():
    """The QWED '2+2=5' cell: a verdict exists (VERIFIED, with a proof) and the fact
    must not be acted on (DENY). The axes are independent."""
    r = classify(fact("ram", "32GB = 2x8GB DDR4"))
    assert (r["status"], r["scope"], r["admission"]) == (ft.VERIFIED, ft.FORMAL, ft.DENY)
    assert r["issues"] == ["arithmetic_mismatch"] and r["proof_ref"]


def test_parenthetical_form_is_only_read_on_memory_predicates():
    """'256GB NVMe (2x8GB)' on a storage fact is a disk and its RAM, not an identity —
    reading it as one would DENY a correct fact."""
    assert ft.check_math("256GB NVMe (2x8GB)", "storage") == []
    assert len(ft.check_math("256GB NVMe (2x8GB)", "ram")) == 1
    assert len(ft.check_math("512GB = 2x256GB mirror", "storage")) == 1   # explicit = always
    assert classify(fact("hardware", "i5, 256GB SSD (2x8GB)"))["status"] == ft.UNVERIFIABLE


# ------------------------------------------------------------------ constraint (Z3)

@needs_z3
@pytest.mark.parametrize("pred,value,verdicts", [
    ("tailscale_ip", "100.117.66.102", [ft.PROVEN_TRUE, ft.PROVEN_TRUE]),
    ("tailscale_ip", "100.63.255.255", [ft.PROVEN_TRUE, ft.PROVEN_FALSE]),   # just below /10
    ("tailscale_ip", "100.64.0.0", [ft.PROVEN_TRUE, ft.PROVEN_TRUE]),        # first address
    ("tailscale_ip", "100.127.255.255", [ft.PROVEN_TRUE, ft.PROVEN_TRUE]),   # last address
    ("tailscale_ip", "100.128.0.0", [ft.PROVEN_TRUE, ft.PROVEN_FALSE]),      # just above
    ("tailscale_ip", "192.168.8.160", [ft.PROVEN_TRUE, ft.PROVEN_FALSE]),
    ("lan_ip", "192.168.8.160", [ft.PROVEN_TRUE, ft.PROVEN_TRUE]),
    ("lan_ip", "192.168.8.209 — (Seneca body, eno1)", [ft.PROVEN_TRUE, ft.PROVEN_TRUE]),
    ("lan_ip", "172.31.255.1", [ft.PROVEN_TRUE, ft.PROVEN_TRUE]),
    ("lan_ip", "172.32.0.1", [ft.PROVEN_TRUE, ft.PROVEN_FALSE]),
    ("lan_ip", "8.8.8.8", [ft.PROVEN_TRUE, ft.PROVEN_FALSE]),
    ("plug_ip", "192.168.8.300", [ft.PROVEN_FALSE]),                         # octet > 255
    ("port", "3000", [ft.PROVEN_TRUE]),
    ("port", "0", [ft.PROVEN_FALSE]),
    ("port", "65535", [ft.PROVEN_TRUE]),
    ("port", "65536", [ft.PROVEN_FALSE]),
    ("lifecycle", "retired", [ft.PROVEN_TRUE]),
    ("lifecycle", "dead", [ft.PROVEN_FALSE]),
])
def test_constraints(pred, value, verdicts):
    checks = ft.check_constraint(pred, value)
    assert [c["verdict"] for c in checks] == verdicts
    assert all(c["engine"] == "z3" for c in checks)


def test_constraint_ignores_prose_and_unrelated_predicates():
    assert ft.check_constraint("lan_ip", "dhcp, not yet assigned") == []
    assert ft.check_constraint("hardware", "192.168.8.1") == []
    assert ft.check_constraint("port", "the usual one") == []
    assert ft.check_constraint("description", "ip 10.0.0.1") == []


@needs_z3
def test_out_of_range_address_is_denied():
    r = classify(fact("tailscale_ip", "192.168.8.160"))
    assert r["status"] == ft.VERIFIED and r["admission"] == ft.DENY
    assert r["issues"] == ["address_out_of_range"]


# ------------------------------------------------------------------ sql (SQLGlot)

@needs_sqlglot
@pytest.mark.parametrize("sql,issues", [
    ("SELECT name FROM nodes WHERE id = 3", []),
    ("SELECT * FROM users WHERE id=1 OR 1=1; DROP TABLE users",
     ["destructive_command", "injection_tautology", "stacked_queries"]),
    ("DELETE FROM users", ["destructive_command"]),
    ("DELETE FROM users WHERE id = 7", []),
    ("UPDATE t SET a = 1", ["destructive_command"]),
    ("TRUNCATE TABLE logs", ["destructive_command"]),
    ("DROP TABLE x", ["destructive_command"]),
    ("SELECT a FROM t WHERE 'x'='x'", ["injection_tautology"]),
])
def test_sql_guard(sql, issues):
    (c,) = ft.check_sql(sql)
    assert c["issues"] == issues
    assert c["verdict"] == (ft.PROVEN_FALSE if issues else ft.PROVEN_TRUE)


@needs_sqlglot
def test_destructive_sql_fact_is_verified_and_denied():
    r = classify(fact("cleanup_sql", "DELETE FROM users WHERE 1=1; DROP TABLE users"))
    assert (r["status"], r["admission"]) == (ft.VERIFIED, ft.DENY)
    assert "destructive_command" in r["issues"] and "stacked_queries" in r["issues"]


def test_sql_candidate_detection():
    assert ft.sql_candidate("report_query", "anything")
    assert ft.sql_candidate("note", "SELECT 1")
    assert not ft.sql_candidate("status", "selected for retirement")
    assert not ft.sql_candidate("mysql_version", "8.0")


# ------------------------------------------------------------------ code (Python AST)

@pytest.mark.parametrize("src,issues", [
    ("import json\nprint(json.dumps({'a': 1}))", []),
    ("import os\nos.system('rm -rf /')", ["forbidden_call:os.system"]),
    ("eval(input())", ["forbidden_call:eval"]),
    ("import subprocess\nsubprocess.run('ls', shell=True)",
     ["shell_injection:subprocess(shell=True)"]),
    ("import subprocess\nsubprocess.run(['ls'], shell=False)", []),
    ("python3 -c 'import shutil; shutil.rmtree(\"/x\")'", ["forbidden_call:shutil.rmtree"]),
])
def test_code_guard(src, issues):
    (c,) = ft.check_code(src)
    assert c["issues"] == issues and c["engine"] == "ast"


def test_code_engine_declines_non_python():
    assert ft.check_code("this is ( not python") == []


@pytest.mark.parametrize("value,verdict", [
    ("29.99", ft.PROVEN_TRUE), ("0", ft.PROVEN_TRUE), ("$4.99 shipped", ft.PROVEN_TRUE),
    ("-5", ft.PROVEN_FALSE), ("1.2.3", ft.PROVEN_FALSE),
])
def test_price_literal(value, verdict):
    (c,) = ft.check_number("price", value)
    assert c["verdict"] == verdict
    assert ft.check_number("price", "call for quote") == []
    assert ft.check_number("ram", "29.99") == []


# ------------------------------------------------------------------ refused routes

@pytest.mark.parametrize("route", ["logic", "consensus", "natural_language"])
def test_fail_open_routes_are_refused_by_name(route):
    with pytest.raises(ft.RouteRefused) as e:
        ft.verify_claim(route, "all nodes that are online have an ip")
    assert "not wired" in str(e.value)
    assert route not in ft.ROUTES


def test_unknown_route_is_refused_not_ignored():
    with pytest.raises(ft.RouteRefused):
        ft.verify_claim("vibes", "x")


def test_module_has_no_llm_or_network_surface():
    """The fail-open in QWED #320 is an unverified translation step. There must be no
    way for one to exist here: no HTTP client, no model client, no subprocess."""
    src = open(ft.__file__).read()
    import ast as _ast
    imported = set()
    for node in _ast.walk(_ast.parse(src)):
        if isinstance(node, _ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, _ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    banned = {"urllib", "http", "requests", "httpx", "socket", "subprocess", "openai",
              "anthropic", "litellm"}
    assert not (imported & banned), imported & banned
    assert "__import__(name)" in src        # the only dynamic import is the engine loader


# ------------------------------------------------------------------ probe evidence

def ver(outcome, age_days, probe="node_storage", observed="x"):
    return {"fact_id": "f", "probe": probe, "observed": observed, "outcome": outcome,
            "ts": iso(NOW - age_days * 86400)}


def test_confirmed_probe_is_world_verified():
    r = classify(fact("storage", "Kingston 256GB", verify_recipe="node_storage"),
                 [ver("confirmed", 2)])
    assert (r["status"], r["scope"], r["admission"]) == (ft.VERIFIED, ft.WORLD, ft.ADMIT)


def test_confirmed_but_stale_is_verified_and_denied():
    """'A confirmed-stale fact is verified but must not drive decisions.' identity class
    = 90 d interval, stale past 3x."""
    r = classify(fact("storage", "Kingston 256GB", verify_recipe="node_storage"),
                 [ver("confirmed", 271)])
    assert (r["status"], r["admission"], r["issues"]) == (ft.VERIFIED, ft.DENY, ["stale_evidence"])
    edge = classify(fact("storage", "Kingston 256GB", verify_recipe="node_storage"),
                    [ver("confirmed", 269)])
    assert edge["admission"] == ft.ADMIT


def test_probe_that_cannot_answer_is_blocked_not_unverifiable():
    r = classify(fact("svc_status", "active", verify_recipe=None),
                 [ver("unknown", 1, probe="service_state")])
    assert r["status"] == ft.BLOCKED and "service_state" in r["blocked_by"]
    assert r["proof_ref"] is None
    never = classify(fact("kernel", "7.0.0", verify_recipe="os_kernel"))
    assert never["status"] == ft.BLOCKED and "no observation yet" in never["blocked_by"]


def test_recent_confirm_survives_a_later_unknown_run():
    r = classify(fact("storage", "x", verify_recipe="node_storage"),
                 [ver("unknown", 0.1), ver("confirmed", 1)])
    assert r["status"] == ft.VERIFIED and "returned unknown" in r["probe"]["latest_run"]


def test_observed_row_counts_as_its_own_observation():
    f = fact("kernel", "7.0.0-42", verify_recipe="os_kernel", source_kind="observed",
             last_confirmed_at=iso(NOW - 3600), fact_class="config")
    assert classify(f)["scope"] == ft.WORLD


def test_no_probe_no_engine_is_unverifiable():
    r = classify(fact("role", "edge firewall"))
    assert (r["status"], r["admission"], r["proof_ref"]) == (ft.UNVERIFIABLE, ft.ADMIT, None)


@needs_z3
def test_blocked_probe_outranks_a_formal_check_but_admission_still_sees_it():
    r = classify(fact("lan_ip", "8.8.8.8", verify_recipe="fleet_inventory"),
                 [ver("unknown", 1, probe="fleet_inventory")])
    assert r["status"] == ft.BLOCKED and r["admission"] == ft.DENY
    assert r["issues"] == ["address_out_of_range"]


def test_contradicted_and_closed_lifecycle_deny():
    r = classify(fact("role", "x", contradicted_at=iso(NOW)))
    assert r["admission"] == ft.DENY and r["issues"] == ["contradicted"]
    op = classify(fact("dc_status", "active", subject="wadjet"),
                  closed_subjects={"wadjet"}, is_operational=lambda p: p.startswith("dc_"))
    assert op["issues"] == ["closed_lifecycle"]
    ident = classify(fact("hardware", "Mac Pro", subject="wadjet"),
                     closed_subjects={"wadjet"}, is_operational=lambda p: p.startswith("dc_"))
    assert ident["admission"] == ft.ADMIT


# ------------------------------------------------------------------ engine missing

def test_missing_engine_is_blocked_never_a_pass(monkeypatch):
    monkeypatch.setitem(ft._ENGINE_CACHE, "z3", None)
    monkeypatch.setitem(ft._ENGINE_CACHE, "sympy", None)
    monkeypatch.setitem(ft._ENGINE_CACHE, "sqlglot", None)
    for f in (fact("tailscale_ip", "100.117.66.102"), fact("ram", "64GB = 4x16GB"),
              fact("audit_sql", "DROP TABLE x")):
        r = classify(f)
        assert r["status"] == ft.BLOCKED and r["proof_ref"] is None, f
        assert r["blocked_by"].startswith("engine:")


# ------------------------------------------------------------------ proof_ref

@needs_sympy
def test_proof_ref_is_deterministic_and_binds_value_identity_and_evidence():
    a = classify(fact("ram", "64GB = 4x16GB"))
    assert a["proof_ref"] == classify(fact("ram", "64GB = 4x16GB"))["proof_ref"]
    assert len(a["proof_ref"]) == 64 and int(a["proof_ref"], 16) >= 0
    different = [classify(fact("ram", "32GB = 2x16GB")),
                 classify(fact("ram", "64GB = 4x16GB", fid="other-id")),
                 classify(fact("ram", "64GB = 4x16GB", subject="thor")),
                 classify(fact("ram", "64GB = 4x16GB  ")),
                 classify(fact("ram", "64GB = 4x16GB", verify_recipe="node_storage"),
                          [ver("confirmed", 1)])]
    refs = {a["proof_ref"]} | {d["proof_ref"] for d in different}
    assert len(refs) == 6


def test_proof_ref_iff_verified_over_every_combination():
    """Test to failure: every (value shape x probe history x contradiction) cell —
    proof_ref is present exactly when status is VERIFIED, and status/admission are only
    ever one of the taxonomy's values."""
    values = [("ram", "64GB = 4x16GB"), ("ram", "32GB = 2x8GB"), ("role", "prose"),
              ("lan_ip", "192.168.8.1"), ("lan_ip", "999.1.1.1"), ("price", "-1"),
              ("x_sql", "DROP TABLE t"), ("x_code", "eval('1')"), ("port", "70000")]
    histories = [None, [ver("confirmed", 1)], [ver("confirmed", 9999)], [ver("unknown", 1)],
                 [ver("unknown", 1), ver("confirmed", 2)], [ver("superseded", 1)]]
    n = 0
    for (pred, val), hist, recipe, contra in itertools.product(
            values, histories, (None, "node_storage"), (None, iso(NOW))):
        r = classify(fact(pred, val, verify_recipe=recipe, contradicted_at=contra), hist)
        assert (r["status"] == ft.VERIFIED) == bool(r["proof_ref"]), (pred, val, hist)
        assert r["status"] in (ft.VERIFIED, ft.BLOCKED, ft.UNVERIFIABLE)
        assert r["admission"] in (ft.ADMIT, ft.DENY)
        assert (r["admission"] == ft.DENY) == bool(r["issues"])
        if contra:
            assert r["admission"] == ft.DENY
        n += 1
    assert n == 216


# ------------------------------------------------------------------ scan + ledger

@pytest.fixture
def state_db(tmp_path):
    p = tmp_path / "state.db"
    c = sqlite3.connect(p)
    c.execute("""CREATE TABLE state_facts (id TEXT, subject TEXT, predicate TEXT, value TEXT,
        valid_from TEXT, valid_to TEXT, last_confirmed_at TEXT, fact_class TEXT,
        source_kind TEXT, verify_recipe TEXT, verify_interval_days REAL, contradicted_at TEXT)""")
    c.execute("""CREATE TABLE fact_verifications (id TEXT, fact_id TEXT, subject TEXT,
        predicate TEXT, probe TEXT, observed TEXT, outcome TEXT, ts TEXT, detail TEXT)""")
    rows = [
        ("f1", "lumen", "ram", "64GB = 4x16GB", None, "identity", None, None),
        ("f2", "lumen", "role", "control plane", None, "identity", None, None),
        ("f3", "lumen", "storage", "Kingston", None, "identity", None, "node_storage"),
        ("f4", "thor", "ram", "32GB = 2x8GB", None, "identity", None, None),
        ("f5", "wadjet", "lifecycle", "retired", None, "config", None, None),
        ("f6", "old", "ram", "8GB = 1x8GB", iso(NOW), "identity", None, None),  # closed row
    ]
    for fid, s, p_, v, vt, fc, sk, vr in rows:
        c.execute("INSERT INTO state_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (fid, s, p_, v, iso(NOW - 86400), vt, None, fc, sk, vr, None, None))
    c.execute("INSERT INTO fact_verifications VALUES ('v1','f3','lumen','storage',"
              "'node_storage','Kingston','unknown',?,NULL)", (iso(NOW - 60),))
    c.commit()
    c.close()
    return str(p)


@needs_sympy
def test_scan_summary_and_ledger_roundtrip(state_db, tmp_path):
    recs = ft.scan(state_db, now=NOW)
    assert [r["fact_id"] for r in recs] == ["f1", "f2", "f3", "f4", "f5"]   # f6 is closed
    by = {r["fact_id"]: r for r in recs}
    assert by["f1"]["status"] == ft.VERIFIED and by["f2"]["status"] == ft.UNVERIFIABLE
    assert by["f3"]["status"] == ft.BLOCKED and by["f4"]["admission"] == ft.DENY
    s = ft.summarize(recs)
    assert s["proof_invariant_ok"] and s["facts"] == 5 and s["status"][ft.BLOCKED] == 1

    ledger = str(tmp_path / "ledger.db")
    ft.write_ledger(recs, ledger, now=NOW)
    c = sqlite3.connect(ledger)
    assert c.execute("SELECT COUNT(*) FROM fact_taxonomy").fetchone()[0] == 5
    assert c.execute("SELECT proof_ref FROM fact_taxonomy WHERE fact_id='f1'").fetchone()[0] \
        == by["f1"]["proof_ref"]
    # the ledger itself refuses a VERIFIED row without a proof and a proof on a non-VERIFIED row
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE fact_taxonomy SET proof_ref = NULL WHERE fact_id = 'f1'")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE fact_taxonomy SET proof_ref = 'abc' WHERE fact_id = 'f2'")
    c.close()

    ok, detail = ft.verify_proof("f1", state_db, ledger)
    assert ok, detail
    # the fact's value changes in lumen_state -> the stored proof no longer verifies
    c = sqlite3.connect(state_db)
    c.execute("UPDATE state_facts SET value = '64GB = 2x32GB' WHERE id = 'f1'")
    c.commit()
    c.close()
    ok, detail = ft.verify_proof("f1", state_db, ledger)
    assert not ok and "mismatch" in detail
    assert ft.verify_proof("nope", state_db, ledger) == (False, "no ledger row")


def test_full_scan_drops_ledger_rows_for_closed_facts(state_db, tmp_path):
    ledger = str(tmp_path / "ledger.db")
    ft.write_ledger(ft.scan(state_db, now=NOW), ledger, now=NOW)
    c = sqlite3.connect(state_db)
    c.execute("UPDATE state_facts SET valid_to = ? WHERE id = 'f2'", (iso(NOW),))
    c.commit()
    c.close()
    ft.write_ledger(ft.scan(state_db, now=NOW), ledger, now=NOW)
    c = sqlite3.connect(ledger)
    assert c.execute("SELECT COUNT(*) FROM fact_taxonomy WHERE fact_id='f2'").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM taxonomy_runs").fetchone()[0] == 2
    c.close()


def test_state_db_is_opened_read_only(state_db):
    c = ft._ro_conn(state_db)
    with pytest.raises(sqlite3.OperationalError):
        c.execute("UPDATE state_facts SET value = 'x'")
    c.close()
    before = open(state_db, "rb").read()
    ft.scan(state_db, now=NOW)
    assert open(state_db, "rb").read() == before


def test_scan_tolerates_a_pre_verify_schema(tmp_path):
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE state_facts (id TEXT, subject TEXT, predicate TEXT, value TEXT,"
              " valid_from TEXT, valid_to TEXT)")
    c.execute("INSERT INTO state_facts VALUES ('a','n','role','x',?,NULL)", (iso(NOW),))
    c.commit()
    c.close()
    (r,) = ft.scan(str(p), now=NOW)
    assert r["status"] == ft.UNVERIFIABLE


def test_cli_engines_and_json(state_db, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ft, "STATE_DB", state_db)
    monkeypatch.setattr(ft, "LEDGER_DB", str(tmp_path / "cli-ledger.db"))
    assert ft.main(["scan", "--write", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["facts"] == 5 and os.path.exists(tmp_path / "cli-ledger.db")
    assert ft.main(["engines"]) == 0
    assert "logic" in json.loads(capsys.readouterr().out)["refused"]
