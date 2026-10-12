"""sentinel-watch: pure-logic tests; no systemd, no wq, no network."""
import importlib.machinery
import importlib.util
import json
import pathlib

P = pathlib.Path(__file__).resolve().parents[1] / "sentinel-watch"
_l = importlib.machinery.SourceFileLoader("sentinel_watch", str(P))
_s = importlib.util.spec_from_loader("sentinel_watch", _l)
sw = importlib.util.module_from_spec(_s)
_l.exec_module(sw)

AQ = (
    "[2592] open     x939 bin-drift  bin-drift/wq3158-reopen.sh               owner=unassigned   2026-10-06T14:08:55Z  lumen-bin primary drift (UNPROVEN) 39m\n"
    '[2977] open     x7   ntfy:lumen-alerts {"topic": "lumen-alerts", "title": "Gite owner=unassigned   2026-10-06T14:09:04Z  Gitea version outdated\n'
    "[2398762] open     x1   foreman-governor usage-stale                              owner=unassigned   2026-10-06T17:10:39Z  governor: usage poll stale\n"
)
WQ = ("[3349] fleet p0 blocked        P0: automated sentinel watch\n"
      "[3500] fleet p2 open           [sentinel] aq/bin-drift: alert source 'bin-drift' has 600 open rows\n"
      "[3400] fleet p2 done           [sentinel] guard/pat-scan: old, already fixed\n")


def test_parse_aq_handles_json_shaped_keys():
    rows = sw.parse_aq(AQ)
    assert len(rows) == 3
    assert rows[1]["source"] == "ntfy:lumen-alerts" and rows[1]["count"] == 7


def test_noisy_aq_source_is_one_finding_and_quiet_ones_are_not():
    assert [f["key"] for f in sw.aq_findings(sw.parse_aq(AQ))] == ["aq/bin-drift"]


def test_closed_tickets_do_not_suppress_a_new_one():
    t = sw.parse_wq_titles(WQ)
    assert any("aq/bin-drift" in x for x in t)
    assert not any("guard/pat-scan" in x for x in t)


def test_plan_filing_dedupes_orders_by_severity_and_caps():
    t = sw.parse_wq_titles(WQ)
    fs = [sw.mk("aq/bin-drift", 2, "t", "d"), sw.mk("guard/pat-scan", 1, "t", "d"),
          sw.mk("a/1", 2, "t", "d"), sw.mk("a/2", 2, "t", "d")]
    new, skipped, over = sw.plan_filing(fs, t, 2)
    assert [f["key"] for f in skipped] == ["aq/bin-drift"]
    assert [f["key"] for f in new] == ["guard/pat-scan", "a/1"]
    assert [f["key"] for f in over] == ["a/2"]


def test_paging_rules():
    h1 = sw.mk("litellm/provider-cooled", 1, "t", "d", human=True)
    p2 = sw.mk("svc/x", 2, "t", "d", human=True)
    p0 = sw.mk("aq/unreadable", 0, "t", "d")
    now = 1_000_000.0
    assert sw.should_page(h1, {}, {}, now)                                   # human-only P1 pages at once
    assert not sw.should_page(p2, {}, {}, now)                               # P2 never pages
    assert not sw.should_page(p0, {"aq/unreadable": {"runs": 1}}, {}, now)   # agent-fixable: ticket first
    assert sw.should_page(p0, {"aq/unreadable": {"runs": 2}}, {}, now)       # survived 2 sweeps: agents did not fix it
    assert not sw.should_page(h1, {}, {h1["key"]: now - 3600}, now)          # 24h cooldown


def test_missing_and_stalled_guard_timers(monkeypatch):
    def fake(unit, *p):
        if unit.endswith(".timer"):
            if unit == "lumen-pat-scan.timer":
                return {"LoadState": "not-found"}
            return {"LoadState": "loaded", "ActiveState": "active", "NextElapseUSecRealtime": "", "NextElapseUSecMonotonic": "0"}
        if unit in ("lumen-leakwatch.service", "lumen-alert-queue.service"):
            return {"LoadState": "loaded", "ActiveState": "active", "Result": "success"}
        return {"LoadState": "loaded", "ActiveState": "inactive", "Result": "success"}   # paired oneshot, idle
    monkeypatch.setattr(sw, "show", fake)
    ctx = {"notes": []}
    keys = {f["key"]: f for f in sw.c_guards(ctx)}
    assert keys["guard/pat-scan"]["sev"] == 1 and "not installed" in keys["guard/pat-scan"]["title"]
    assert keys["guard/bin-drift-rescue"]["sev"] == 2 and "stalled" in keys["guard/bin-drift-rescue"]["title"]
    assert any("overlay-drift-guard" in n for n in ctx["notes"])            # known-absent is noted, not ticketed
    assert not any("overlay" in k for k in keys)


def test_a_firing_core_probe_is_p1(tmp_path, monkeypatch):
    f = tmp_path / "state.json"
    f.write_text('{"_fires": {"probe-storage-integrity": {"since": 1790000000, "pages": 16}, "probe-kb-hygiene": {"since": 1790000000, "pages": 1}}}')
    monkeypatch.setattr(sw, "PROBE_STATE", str(f))
    got = {x["key"]: x["sev"] for x in sw.c_probes({"now": 1791000000.0})}
    assert got == {"probe/probe-storage-integrity": 1, "probe/probe-kb-hygiene": 2}


def test_check_mode_exit_codes(monkeypatch):
    monkeypatch.setattr(sw, "COLLECTORS", {"t": lambda ctx: [sw.mk("x/y", 2, "t", "d")]})
    assert sw.main(["--check", "x/y", "--quiet"]) == 1
    assert sw.main(["--check", "other/key", "--quiet"]) == 0


def _wire(tmp_path, monkeypatch, calls):
    monkeypatch.setattr(sw, "STATE", str(tmp_path / "s.json"))
    monkeypatch.setattr(sw, "LOG", str(tmp_path / "l.log"))
    monkeypatch.setattr(sw, "COLLECTORS", {"t": lambda ctx: [sw.mk("x/y", 2, "t", "d")]})

    def fake_sh(cmd, timeout=30):
        calls.append(cmd)
        return (0, "wq#4242\n" if "add" in cmd else "", "")
    monkeypatch.setattr(sw, "sh", fake_sh)


def test_dry_run_files_and_writes_nothing(tmp_path, monkeypatch):
    calls = []
    _wire(tmp_path, monkeypatch, calls)
    assert sw.main(["--dry-run", "--quiet"]) == 4
    assert not any("add" in c for c in calls)
    assert not (tmp_path / "s.json").exists() and not (tmp_path / "l.log").exists()


def test_real_run_files_one_ticket_with_a_liveness_probe_and_logs(tmp_path, monkeypatch):
    calls = []
    _wire(tmp_path, monkeypatch, calls)
    assert sw.main(["--quiet"]) == 4
    adds = [c for c in calls if "add" in c]
    assert len(adds) == 1 and "--liveness" in adds[0]
    assert "[sentinel] x/y:" in adds[0][adds[0].index("--title") + 1]
    assert "wq#4242" in (tmp_path / "l.log").read_text()
    assert (tmp_path / "s.json").exists()


def _hygiene_calls(calls):
    return [c for c in calls if c[-1] == sw.AQ_HYGIENE]


def test_real_run_runs_aq_hygiene_once_per_day(tmp_path, monkeypatch):
    calls = []
    _wire(tmp_path, monkeypatch, calls)
    sw.main(["--quiet"])
    sw.main(["--quiet"])
    assert len(_hygiene_calls(calls)) == 1
    assert "aq_hygiene_last" in json.loads((tmp_path / "s.json").read_text())


def test_check_dry_run_and_other_collectors_never_run_aq_hygiene(tmp_path, monkeypatch):
    calls = []
    _wire(tmp_path, monkeypatch, calls)
    sw.main(["--check", "x/y", "--quiet"])
    sw.main(["--dry-run", "--quiet"])
    sw.main(["--only", "disk", "--quiet"])
    assert _hygiene_calls(calls) == []


def test_failed_aq_hygiene_is_a_finding_that_check_mode_still_sees(tmp_path, monkeypatch):
    calls = []
    _wire(tmp_path, monkeypatch, calls)
    monkeypatch.setattr(sw, "COLLECTORS", {"aq": sw.c_aq})

    def failing_sh(cmd, timeout=30):
        calls.append(cmd)
        if cmd[-1] == sw.AQ_HYGIENE:
            return (1, "", "database is locked")
        return (0, "", "")
    monkeypatch.setattr(sw, "sh", failing_sh)
    sw.main(["--quiet"])
    assert sw.main(["--check", "aq/hygiene-failed", "--quiet"]) == 1
    st = json.loads((tmp_path / "s.json").read_text())
    assert "aq_hygiene_last" not in st and "locked" in st["aq_hygiene_err"]


def test_ticket_id_is_found_by_title_when_wq_add_output_has_no_id(monkeypatch):
    def fake_sh(cmd, timeout=30):
        if "add" in cmd:
            return (0, "ok\n", "")
        return (0, "[4300] fleet p2 open           [sentinel] x/y: something\n", "")
    monkeypatch.setattr(sw, "sh", fake_sh)
    assert sw.file_ticket(sw.mk("x/y", 2, "something", "d"), "run1") == "wq#4300"
