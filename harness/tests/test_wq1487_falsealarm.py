"""wq#1487: fire-mode auto-close must always carry evidence (no evidence -> evidence
gate refusal -> CRASH loop, the wq#1424 shape), and CRASH pages dedupe per
(ticket, error-line)."""
import json, os, sqlite3, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import fire_gate  # noqa: E402


def _aqdb(tmp_path, status):
    p = tmp_path / "alerts.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE alerts(id INTEGER PRIMARY KEY, status TEXT, resolved_ts TEXT, note TEXT)")
    c.execute("INSERT INTO alerts VALUES(1069,?,?,?)", (status, "2026-09-07T23:23:19Z", "auto-cleared"))
    c.commit(); c.close()
    return str(p)


DETAIL = "Probe: probe-synthetic-llm\naq row: #1069 (source=probe, priority=high)\n"


def test_aq_fallback_cleared_carries_evidence(tmp_path):
    cleared, ev = fire_gate.probe_cleared(DETAIL, aq_db=_aqdb(tmp_path, "resolved"))
    assert cleared is True
    assert ev is not None and ev["kind"] == "aq_row" and ev["aq_id"] == 1069
    assert ev["aq_status"] == "resolved"
    assert "cmd" in ev and "exit_code" in ev  # notes template reads these via .get


def test_aq_fallback_still_open_no_evidence(tmp_path):
    cleared, ev = fire_gate.probe_cleared(DETAIL, aq_db=_aqdb(tmp_path, "open"))
    assert cleared is False and ev is None


def test_crash_page_dedup(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREMAN_CRASH_PAGE_STATE", str(tmp_path / "crash.json"))
    monkeypatch.setenv("FOREMAN_CRASH_PAGE_DEDUP_S", "3600")
    import importlib, fworker
    importlib.reload(fworker)
    sent = []
    monkeypatch.setattr(fworker, "ntfy", lambda t, m, priority="default": sent.append((t, m, priority)))
    monkeypatch.setattr(fworker, "log", lambda *a, **k: None)
    err = "RuntimeError: wq done 1424 refused (exit 2)"
    for _ in range(5):
        fworker.ntfy_crash(1424, err, "wq#1424 crashed: " + err)
    assert len(sent) == 1, sent                       # 5 identical crashes -> 1 page
    fworker.ntfy_crash(1424, "ValueError: other", "different")
    assert len(sent) == 2                             # different error -> pages
    fworker.ntfy_crash(1425, err, "other ticket")
    assert len(sent) == 3                             # different ticket -> pages
    st = json.load(open(tmp_path / "crash.json"))
    k = [k for k in st if k.startswith("1424:")]
    assert any(v["suppressed"] == 4 for v in (st[x] for x in k))
    # window expiry -> re-page once
    for x in k:
        st[x]["paged_at"] = time.time() - 7200
    json.dump(st, open(tmp_path / "crash.json", "w"))
    fworker.ntfy_crash(1424, err, "again")
    assert len(sent) == 4
