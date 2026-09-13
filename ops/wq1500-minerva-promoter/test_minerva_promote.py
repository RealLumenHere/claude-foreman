#!/usr/bin/env python3
"""test_minerva_promote.py — wq#1500 acceptance replay for the live promoter.

The promoter itself (minerva_promote.py, design PROMOTER_JUDGE_REPAIR_DESIGN.md §2a) lives in
the minerva repo, whose tests/ dir is excluded from the worker-readable mirror. This is the
foreman-side test: it imports the module from the sweep-gated mirror (MINERVA_REF, default
/home/lumen/research/minerva-ref; point it at /home/lumen/minerva to test the live copy),
pins every path constant to a scratch dir, and drives the REAL /home/lumen/bin/wq against a
scratch WQ_DB. Nothing live is read or written.

Covers: eligibility (deploy_ready|planned|staged only; pre-`since` rows and green-applied rows
skipped), CVE -> [SEC] pri 1, GHSA in the intel title -> pri 1, YELLOW -> 2, RED -> 3,
unplanned -> 3, lane fleet, grounding block + minerva_proposal=<id> marker in detail, published
proposal.md, idempotent re-run, marker recovery when the column is wiped, MINERVA_PROMOTE=0,
same-product upgrade collapse.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

MINERVA_REF = os.environ.get("MINERVA_REF", "/home/lumen/research/minerva-ref")
WQ_BIN = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")

TMP = Path(tempfile.mkdtemp(prefix="wq1500-promote-"))
os.environ["WQ_DB"] = str(TMP / "queue.db")
os.environ["WQ_AGENT"] = "test-minerva-promoter"
os.environ["MINERVA_PUBLISH_ROOT"] = str(TMP / "published")
for k in ("MINERVA_PROMOTE", "MINERVA_PROMOTE_LANE", "MINERVA_PROMOTE_SINCE"):
    os.environ.pop(k, None)

sys.path.insert(0, MINERVA_REF)
import minerva_promote as mp  # noqa: E402

mp.DB = str(TMP / "minerva.db")
mp.WQ = WQ_BIN
mp.PROPOSALS_DIR = TMP / "pending"
mp.FLEET_STACK = str(TMP / "fleet_stack.json")
# Keep the test off the live factory module: same strings as the module's own fallback.
mp._approval_cmds = lambda plan_id: (f"touch /home/lumen/instructions/approvals/F{plan_id}.approved",
                                     f"minerva-factory apply {plan_id} --approved")

SCHEMA = [
    """CREATE TABLE proposals (id TEXT PRIMARY KEY, parent_id TEXT, title TEXT, source_intel_id TEXT,
       current_state TEXT, proposed_change TEXT, effort TEXT, impact TEXT, risk TEXT, rollback TEXT,
       status TEXT DEFAULT 'pending', eval_result TEXT, eval_passed INTEGER DEFAULT 0,
       created_at TEXT, decided_at TEXT, deployed_at TEXT)""",
    """CREATE TABLE plans (id TEXT PRIMARY KEY, proposal_id TEXT, lane TEXT, change_type TEXT,
       params TEXT, targets TEXT, draft_diff TEXT, apply_cmd TEXT, health_cmd TEXT, rollback_cmd TEXT,
       gate_result TEXT, status TEXT DEFAULT 'planned', notes TEXT, created_at TEXT, applied_at TEXT,
       reverted INTEGER DEFAULT 0)""",
    """CREATE TABLE classified_intel (id TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT,
       audience_relevance REAL, self_relevance REAL, category TEXT, reasoning TEXT, ts TEXT,
       processed INTEGER DEFAULT 0)""",
    """CREATE TABLE verification_log (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, input_text TEXT,
       context_ref TEXT, citation_score REAL, judge_score REAL, final_score REAL, passed INTEGER,
       rejected_reason TEXT, model TEXT, ts TEXT, judge_rationale TEXT, error_kind TEXT)""",
    """CREATE TABLE changelog (id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT, change_type TEXT,
       target_path TEXT, diff TEXT, applied_by TEXT DEFAULT 'minerva', applied_at TEXT,
       reverted INTEGER DEFAULT 0, revert_reason TEXT)""",
]

FAILS = []
N = [0]


def check(cond, what):
    N[0] += 1
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        FAILS.append(what)


def seed():
    c = sqlite3.connect(mp.DB)
    for s in SCHEMA:
        c.execute(s)
    intel = [
        ("i-gitea", "github", "Gitea 1.27.3 released", "https://example/gitea", "tool"),
        ("i-uvicorn", "github", "GHSA-abcd-efgh-ijkl uvicorn header parsing", "https://example/uv", "tool"),
        ("i-renovate", "blog", "Renovate adds APK datasource", "https://example/ren", "tool"),
    ]
    c.executemany("INSERT INTO classified_intel(id,source,title,url,category) VALUES (?,?,?,?,?)", intel)
    props = [
        # id, title, intel, current_state, proposed_change, status, created_at
        ("a1a1a1a1a1a1", "Upgrade Gitea to 1.27.3 and verify CVE-2026-66877", "i-gitea",
         "Gitea 1.27.2 runs on Anubis", "pull 1.27.3, restart, verify", "planned", "2026-09-10T01:00:00"),
        ("b2b2b2b2b2b2", "Enable Renovate APK datasource for Alpine tracking", "i-renovate",
         "renovate-gitea timer runs daily", "add apk datasource to renovate.json", "staged", "2026-09-10T02:00:00"),
        ("c3c3c3c3c3c3", "Rotate Grafana dashboard provisioning", None,
         "dashboards provisioned from git", "add provisioning reload", "deploy_ready", "2026-09-10T03:00:00"),
        ("d4d4d4d4d4d4", "Bump uvicorn to 0.52.3 across FastAPI services", "i-uvicorn",
         "uvicorn 0.50 in three services", "bump pin, restart", "planned", "2026-09-10T04:00:00"),
        ("e5e5e5e5e5e5", "Accepted but not yet evaluated", None, "x", "y", "accepted", "2026-09-10T05:00:00"),
        ("f6f6f6f6f6f6", "Add litellm route for new model", None, "x", "y", "planned", "2026-09-10T06:00:00"),
        ("07070707a7a7", "Old parked proposal from before grounding", None, "x", "y", "planned", "2026-08-01T00:00:00"),
        ("18181818b8b8", "Upgrade Langfuse to v4.26.0 for evaluator trace linking", None,
         "langfuse 4.20", "bump compose tag", "planned", "2026-09-11T01:00:00"),
        ("29292929c9c9", "Upgrade Langfuse to v4.30.0 and enable experiments grid", None,
         "langfuse 4.20", "bump compose tag", "planned", "2026-09-11T02:00:00"),
    ]
    c.executemany("INSERT INTO proposals(id,title,source_intel_id,current_state,proposed_change,status,created_at,"
                  "effort,impact,risk,rollback) VALUES (?,?,?,?,?,?,?,'low','high','low','revert tag')", props)
    plans = [
        ("P-a1", "a1a1a1a1a1a1", "red", "service_change", "planned", '{"lane_reasons":["restart of crown service"]}'),
        ("P-b2", "b2b2b2b2b2b2", "yellow", "config_edit", "staged", '{"lane_reasons":["config edit"]}'),
        ("P-d4", "d4d4d4d4d4d4", "red", "code_edit", "planned", "{}"),
        ("P-f6", "f6f6f6f6f6f6", "green", "litellm_route_add", "applied", "{}"),
        ("P-18", "18181818b8b8", "red", "service_change", "planned", "{}"),
        ("P-29", "29292929c9c9", "red", "service_change", "planned", "{}"),
    ]
    c.executemany("INSERT INTO plans(id,proposal_id,lane,change_type,status,gate_result,created_at,notes,targets,"
                  "apply_cmd,rollback_cmd) VALUES (?,?,?,?,?,?, '2026-09-10T09:00:00','planner note','anubis',"
                  "'docker compose pull','docker compose down')", plans)
    c.execute("INSERT INTO verification_log(subject,input_text,citation_score,judge_score,final_score,passed,ts,"
              "judge_rationale) VALUES ('proposal:Gitea','Gitea 1.27.2 runs on Anubis',0.9,0.8,0.85,1,"
              "'2026-09-10T00:30:00','grounded in RUNNING VERSIONS')")
    c.commit()
    c.close()
    Path(mp.FLEET_STACK).write_text(json.dumps({"repos": {"langfuse/langfuse": {}, "go-gitea/gitea": {}}}))


def wq_tasks():
    w = sqlite3.connect(f"file:{os.environ['WQ_DB']}?mode=ro", uri=True)
    w.row_factory = sqlite3.Row
    rows = w.execute("SELECT id, lane, pri, title, detail, status FROM tasks ORDER BY id").fetchall()
    w.close()
    return rows


def wq_ticket(pid):
    c = sqlite3.connect(mp.DB)
    r = c.execute("SELECT wq_ticket FROM proposals WHERE id=?", (pid,)).fetchone()
    c.close()
    return r[0] if r else None


def task(tid):
    for r in wq_tasks():
        if r["id"] == tid:
            return r
    return None


def main():
    seed()
    mp.migrate()
    cols = {r[1] for r in sqlite3.connect(mp.DB).execute("PRAGMA table_info(proposals)")}
    check("wq_ticket" in cols, "migrate() adds additive proposals.wq_ticket column")

    tally = mp.run(limit=200)
    print("run#1 tally:", tally)
    tasks = wq_tasks()
    check(tally.get("added") == 5, f"first run files 5 tickets (gitea, renovate, grafana, uvicorn, langfuse canonical); got {tally}")
    check(tally.get("merged") == 1, "second Langfuse upgrade collapses into the canonical Langfuse ticket")
    check(tally.get("green-applied") == 1, "green-applied plan is not promoted")
    check("accepted" not in [t["title"] for t in tasks] and wq_ticket("e5e5e5e5e5e5") is None,
          "status=accepted proposal is not eligible")
    check(wq_ticket("07070707a7a7") is None, "row created before MINERVA_PROMOTE_SINCE is not backfilled")
    check(all(t["lane"] == "fleet" for t in tasks), "every promoted ticket is on lane fleet")

    g = task(wq_ticket("a1a1a1a1a1a1"))
    check(g is not None and g["pri"] == 1 and g["title"].startswith("[SEC] "), "CVE in title -> pri 1 with [SEC] prefix")
    check(g is not None and "minerva_proposal=a1a1a1a1a1a1" in g["detail"], "detail carries minerva_proposal=<id> marker")
    check(g is not None and "## Source grounding" in g["detail"] and "Gitea 1.27.3 released" in g["detail"],
          "grounding block names the source intel")
    check(g is not None and "## Verification" in g["detail"] and "judge=0.8" in g["detail"]
          and "grounded in RUNNING VERSIONS" in g["detail"],
          "verify scores + judge rationale read back from verification_log")
    check(g is not None and "RED:" in g["detail"] and "lane=red" in g["detail"], "RED plan -> hand-apply instructions")
    check(g is not None and "/home/lumen/minerva" not in g["detail"], "ticket body never cites the excluded minerva root")
    pub = TMP / "published" / "a1a1a1a1a1a1"
    check((pub / "proposal.md").exists() and (pub / "plan.md").exists(), "proposal.md + plan.md published under PUBLISH_ROOT")

    u = task(wq_ticket("d4d4d4d4d4d4"))
    check(u is not None and u["pri"] == 1, "GHSA id in the source intel title -> pri 1")

    r = task(wq_ticket("b2b2b2b2b2b2"))
    check(r is not None and r["pri"] == 2 and not r["title"].startswith("[SEC]"), "YELLOW non-security -> pri 2")
    check(r is not None and "YELLOW:" in r["detail"] and "FP-b2.approved" in r["detail"],
          "YELLOW ticket carries the F<plan>.approved approval step")

    gr = task(wq_ticket("c3c3c3c3c3c3"))
    check(gr is not None and gr["pri"] == 3 and "No factory plan yet" in gr["detail"], "unplanned deploy_ready -> pri 3")

    lf_new, lf_old = wq_ticket("29292929c9c9"), wq_ticket("18181818b8b8")
    check(lf_new is not None and lf_new == lf_old, "both Langfuse proposals link to one ticket")
    lf = task(lf_new)
    check(lf is not None and "v4.30.0" in lf["title"], "newest proposal is the canonical Langfuse ticket")

    before = len(wq_tasks())
    tally2 = mp.run(limit=200)
    print("run#2 tally:", tally2)
    check(len(wq_tasks()) == before and tally2.get("added") is None, "second run files nothing (idempotent on wq_ticket)")

    c = sqlite3.connect(mp.DB)
    c.execute("UPDATE proposals SET wq_ticket=NULL WHERE id='a1a1a1a1a1a1'")
    c.commit()
    c.close()
    tid, action = mp.promote(_row("a1a1a1a1a1a1"))
    check(action == "relinked" and tid == g["id"] and wq_ticket("a1a1a1a1a1a1") == g["id"],
          "wiped column recovers the same ticket via the detail marker (no re-file)")
    check(len(wq_tasks()) == before, "marker recovery filed no new ticket")

    os.environ["MINERVA_PROMOTE"] = "0"
    c = sqlite3.connect(mp.DB)
    c.execute("INSERT INTO proposals(id,title,status,created_at) VALUES ('3a3a3a3a3a3a','Kill switch probe','planned','2026-09-12T00:00:00')")
    c.commit()
    c.close()
    tid, action = mp.promote(_row("3a3a3a3a3a3a"))
    os.environ.pop("MINERVA_PROMOTE")
    check(tid is None and action == "disabled" and len(wq_tasks()) == before, "MINERVA_PROMOTE=0 files nothing")

    print(f"\n{N[0] - len(FAILS)}/{N[0]} checks passed; scratch={TMP}")
    if FAILS:
        for f in FAILS:
            print("  FAILED:", f)
        return 1
    shutil.rmtree(TMP, ignore_errors=True)
    return 0


def _row(pid):
    c = sqlite3.connect(mp.DB)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
    c.close()
    return r


if __name__ == "__main__":
    sys.exit(main())
