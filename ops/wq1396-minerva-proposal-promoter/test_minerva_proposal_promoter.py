#!/usr/bin/env python3
"""End-to-end proof of the wq#1396 minerva proposal promoter.

Runs minerva_proposal_promoter.py as a real subprocess against a scratch
minerva-shaped sqlite DB and the real /home/lumen/bin/wq pointed at a scratch
WQ_DB (env var, honored by wq's own `DB = os.environ.get("WQ_DB", ...)`).
Nothing here touches the live minerva.db or the live work queue.

    python3 ops/wq1396-minerva-proposal-promoter/test_minerva_proposal_promoter.py

Exits 0 when every case passes.
"""
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROMOTER = os.path.join(HERE, "minerva_proposal_promoter.py")
WQ_BIN = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")

PROPOSALS_SCHEMA = """
CREATE TABLE proposals (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    title TEXT, source_intel_id TEXT,
    current_state TEXT, proposed_change TEXT,
    effort TEXT, impact TEXT, risk TEXT, rollback TEXT,
    status TEXT DEFAULT 'pending',
    eval_result TEXT, eval_passed INTEGER DEFAULT 0,
    created_at TEXT, decided_at TEXT, deployed_at TEXT,
    decision_reason TEXT
)
"""

FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def seed_minerva_db(path):
    con = sqlite3.connect(path)
    con.execute(PROPOSALS_SCHEMA)
    con.executemany(
        "INSERT INTO proposals (id,title,source_intel_id,current_state,proposed_change,"
        "risk,rollback,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("p-cve", "Upgrade Gitea to 1.27.3 and verify CVE-2026-66877",
             "intel-1", "Gitea runs old version.", "Bump Gitea, add version check.",
             "M", "Restore prior binary.", "staged", "2026-09-03T06:00:00Z"),
            ("p-feature", "Update Langfuse to v4.26.0 for evaluator trace linking",
             "intel-2", "Langfuse is behind.", "Bump the container tag.",
             "L", "Roll back the tag.", "deploy_ready", "2026-09-03T06:05:00Z"),
            ("p-pending", "Not ready yet", "intel-3", "n/a", "n/a",
             "L", "n/a", "pending", "2026-09-03T06:10:00Z"),
        ],
    )
    con.commit()
    con.close()


def run_promoter(minerva_db, wq_db, extra=()):
    return subprocess.run(
        [sys.executable, PROMOTER, "--db", minerva_db, "--wq-bin", WQ_BIN,
         "--wq-db", wq_db, *extra],
        capture_output=True, text=True, timeout=60,
    )


def fetchall(db, query, params=()):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(query, params).fetchall()]
    finally:
        con.close()


def main():
    with tempfile.TemporaryDirectory() as td:
        minerva_db = os.path.join(td, "minerva.db")
        wq_db = os.path.join(td, "queue.db")
        seed_minerva_db(minerva_db)

        out1 = run_promoter(minerva_db, wq_db)
        check("first run exits 0", out1.returncode == 0, out1.stdout + out1.stderr)

        props = {r["id"]: r for r in fetchall(minerva_db, "SELECT * FROM proposals")}
        check("pending proposal not promoted", props["p-pending"]["ticket"] in (None, ""))
        check("cve proposal got a ticket", bool(props["p-cve"]["ticket"]))
        check("feature proposal got a ticket", bool(props["p-feature"]["ticket"]))

        if props["p-cve"]["ticket"]:
            row = fetchall(wq_db, "SELECT * FROM tasks WHERE id=?", (props["p-cve"]["ticket"],))[0]
            check("cve proposal filed P1", row["pri"] == 1, f"pri={row['pri']}")
            check("cve proposal filed to build lane", row["lane"] == "build")
            check("cve archetype standard-engineering", row["archetype"] == "standard-engineering")
            check("cve detail carries rollback", "Restore prior binary" in row["detail"])

        if props["p-feature"]["ticket"]:
            row = fetchall(wq_db, "SELECT * FROM tasks WHERE id=?", (props["p-feature"]["ticket"],))[0]
            check("feature proposal filed P3", row["pri"] == 3, f"pri={row['pri']}")

        n_tasks_after_first = len(fetchall(wq_db, "SELECT * FROM tasks"))

        out2 = run_promoter(minerva_db, wq_db)
        check("second run exits 0", out2.returncode == 0, out2.stdout + out2.stderr)
        n_tasks_after_second = len(fetchall(wq_db, "SELECT * FROM tasks"))
        check("second run files no new tickets (ticket column skip)",
              n_tasks_after_second == n_tasks_after_first,
              f"{n_tasks_after_first} -> {n_tasks_after_second}")

        # Crash recovery: a proposal with a matching ticket already in queue.db
        # (marker in detail) but ticket column still unset must backfill, not refile.
        con = sqlite3.connect(minerva_db)
        con.execute("INSERT INTO proposals (id,title,proposed_change,status,created_at) "
                    "VALUES (?,?,?,?,?)",
                    ("p-crash", "Recovered proposal title", "do the thing",
                     "staged", "2026-09-03T06:20:00Z"))
        con.commit()
        con.close()
        wqcon = sqlite3.connect(wq_db)
        wqcon.execute(
            "INSERT INTO tasks (lane,pri,title,detail,status,created_at) VALUES (?,?,?,?,?,?)",
            ("build", 3, "Recovered proposal title",
             "do the thing\n\n(auto-filed by minerva-proposal-promoter from minerva proposal p-crash, status=staged)",
             "open", "2026-09-03T06:21:00Z"),
        )
        wqcon.commit()
        crash_ticket_id = wqcon.execute("SELECT last_insert_rowid()").fetchone()[0]
        wqcon.close()
        n_tasks_before_crash_run = len(fetchall(wq_db, "SELECT * FROM tasks"))

        out3 = run_promoter(minerva_db, wq_db)
        check("crash-recovery run exits 0", out3.returncode == 0, out3.stdout + out3.stderr)
        n_tasks_after_crash_run = len(fetchall(wq_db, "SELECT * FROM tasks"))
        check("crash-recovery backfills without refiling",
              n_tasks_after_crash_run == n_tasks_before_crash_run,
              f"{n_tasks_before_crash_run} -> {n_tasks_after_crash_run}")
        crash_row = fetchall(minerva_db, "SELECT * FROM proposals WHERE id=?", ("p-crash",))[0]
        check("crash-recovery writes back the found ticket id",
              crash_row["ticket"] == str(crash_ticket_id),
              f"ticket={crash_row['ticket']!r} expected={crash_ticket_id}")

    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("\nall checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
