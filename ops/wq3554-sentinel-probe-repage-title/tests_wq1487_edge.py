"""wq#1487 test-to-failure for sentinel_probes.main():
 (ii) critical FAIL pages only if the live re-probe also fails;
 (iii) a cleared condition auto-resolves its rows + stops re-paging;
 (iv) a REAL failure still pages, re-pages after REPAGE_S, RECOVERED on PASS.
Runs fully offline: fake probes, captured ntfy/gatus, temp aq db, temp state."""
import json, os, sqlite3, sys, tempfile, time

os.environ["SENTINEL_RETRY_SETTLE_S"] = "0"
os.environ["SENTINEL_REPAGE_S"] = "100"
sys.path.insert(0, "/home/lumen/sentinel/probes")
sys.path.insert(0, "/home/lumen/agents/alert-queue")
tmp = tempfile.mkdtemp()
os.environ["AQ_DB"] = os.path.join(tmp, "alerts.db")
import aq
import sentinel_probes as sp
from pathlib import Path

sp.STATE = Path(tmp) / "state.json"
sent = []
sp.ntfy_urgent = lambda t, m: sent.append(("URGENT", t))
sp._http = lambda *a, **k: sent.append(("HTTP", k.get("headers", {}).get("Title")))
sp.push_gatus = lambda *a, **k: None
sp.time.sleep = lambda s: None

calls = {"n": 0}
mode = {"v": "pass"}
def fake_probe():
    calls["n"] += 1
    if mode["v"] == "pass": return True, "ok", False
    if mode["v"] == "fail": return False, "down", True
    if mode["v"] == "flap":  # first read FAIL, re-probe PASS
        return (False, "timeout", True) if calls["n"] % 2 == 1 else (True, "ok", False)
    raise RuntimeError("boom")
sp.PROBES = [("probe-fake", fake_probe, False)]
sp._state = lambda: json.loads(sp.STATE.read_text()) if sp.STATE.exists() else {}
sp._save = lambda s: sp.STATE.write_text(json.dumps(s))

def urgent(): return [t for k, t in sent if k == "URGENT"]
def rows(): return sqlite3.connect(os.environ["AQ_DB"]).execute("select key,status,count from alerts order by id").fetchall()
def seed_downstream():
    for k in ("FLEET FIRE: probe-fake unanswered x3 -> wq#9", "Gatus: 5-probes/probe-fake", "SENTINEL CRITICAL: probe-fake"):
        aq.ingest(source="ntfy:lumen-alerts", key=k, title=k)

# (ii) transient: FAIL then PASS on re-probe -> no page, no aq row
mode["v"] = "flap"; calls["n"] = 0; sp.main()
assert urgent() == [], urgent(); assert rows() == [], rows()
print("PASS (ii) transient FAIL re-probed PASS -> no page")

# (iv) real failure: FAIL + FAIL -> exactly one urgent page, aq row open
mode["v"] = "fail"; sp.main()
assert urgent() == ["SENTINEL CRITICAL: probe-fake"], urgent()
assert rows() == [("probe-fake", "open", 1)], rows()
print("PASS (iv) real failure pages once")

# (iii-a) persists: 3 more cycles within REPAGE_S -> no more pages, count climbs
for _ in range(3): sp.main()
assert len(urgent()) == 1, urgent(); assert rows()[0][2] == 4
print("PASS (iii-a) persisting failure does not re-page within window")

# (iv-b) window expiry -> one re-page, fixed title (no duration -- wq#3554)
st = json.loads(sp.STATE.read_text()); st["_fires"]["probe-fake"]["paged_at"] -= 200; st["_fires"]["probe-fake"]["since"] -= 200
sp.STATE.write_text(json.dumps(st)); sp.main()
assert len(urgent()) == 2 and urgent()[-1] == "SENTINEL CRITICAL (still failing): probe-fake", urgent()
print("PASS (iv-b) re-page after REPAGE_S")

# (iv-c) wq#3554: a SECOND re-page must use the SAME title as the first, so aq's
# title-keyed ntfy ingest updates one row per probe instead of opening a new row per
# re-page (137 rows / 143 repeats in alerts.db before this fix, 3 open for one probe).
st = json.loads(sp.STATE.read_text()); st["_fires"]["probe-fake"]["paged_at"] -= 200
sp.STATE.write_text(json.dumps(st)); sp.main()
assert len(urgent()) == 3 and urgent()[-1] == urgent()[-2], urgent()
print("PASS (iv-c) repage title stays fixed across repages (duration moved to body)")

# (iii-b) recovery: PASS -> RECOVERED notice, own row + downstream FLEET FIRE/Gatus/CRITICAL rows resolved
seed_downstream(); mode["v"] = "pass"; sp.main()
assert ("HTTP", "SENTINEL RECOVERED: probe-fake") in sent, sent
assert all(s == "resolved" for _, s, _ in rows()), rows()
assert "probe-fake" not in json.loads(sp.STATE.read_text())["_fires"]
print("PASS (iii-b) recovery auto-resolves", len(rows()), "rows (own + downstream) and clears fire state")

# (iii-c) no re-page of a resolved fire on a later PASS cycle
n = len(sent); sp.main(); assert len(sent) == n
print("PASS (iii-c) resolved fire stays quiet")

# probe crash -> non-critical FAIL, no page
mode["v"] = "crash"; sp.main(); assert len(urgent()) == 3
print("PASS crash path never pages urgent")
print("ALL PASS")
