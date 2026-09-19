"""wq#2032 verify-pass probe: PR#650's part-3 sweep
(`ops/wq2027-intel-proposal-source-grounding/sweep_empty_source_proposals.py`)
filters `WHERE p.status = 'pending'`, but the only code path that writes a
proposal row -- `save_proposal()` in `minerva_proposal.py` -- inserts the
LLM's own `decision` field (accept|reject|defer, required by PROMPT_TMPL)
directly into that same `status` column:

    proposal.get('decision', 'pending'),   # minerva_proposal.py save_proposal()

so `status` is 'accept'/'reject'/'defer' in every real row; the literal
string 'pending' is only ever the SQL schema DEFAULT, never an inserted
value. This probe builds a scratch sqlite db with the exact `proposals`
schema PR#650's own precedent test uses
(`ops/wq1396-minerva-proposal-promoter/test_minerva_proposal_promoter.py`,
`status TEXT DEFAULT 'pending'`), seeds ONE row shaped exactly like the
named defect proposal 37cb4ce26a9b (source `classified_intel` row with
NULL url/summary, `status='accept'` -- the LLM decided to adopt the
fabricated repo, which is the whole point of the defect wq#2027 exists to
close) and runs the real, unmodified sweep script against it as a
subprocess.

Run from repo root: python3 reports/2032_probe_sweep_status_never_pending.py
"""
import os
import sqlite3
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SWEEP = os.path.join(HERE, "..", "ops", "wq2027-intel-proposal-source-grounding",
                      "sweep_empty_source_proposals.py")


def seed(db_path, status):
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE classified_intel (
            id TEXT PRIMARY KEY, source TEXT, title TEXT, reasoning TEXT,
            category TEXT, self_relevance REAL, audience_relevance REAL,
            processed INTEGER DEFAULT 0, url TEXT, summary TEXT
        )""")
    con.execute("""
        CREATE TABLE proposals (
            id TEXT PRIMARY KEY, parent_id TEXT, title TEXT, source_intel_id TEXT,
            current_state TEXT, proposed_change TEXT, effort TEXT, impact TEXT,
            risk TEXT, rollback TEXT, status TEXT DEFAULT 'pending',
            eval_result TEXT, created_at TEXT, decision_reason TEXT
        )""")
    con.execute(
        "INSERT INTO classified_intel (id, source, title, reasoning, category, "
        "self_relevance, audience_relevance, processed, url, summary) VALUES "
        "('intel-1','github','Roshanrameshhub/AgentForge','HINT: orchestration "
        "patterns','tool',0.9,0.5,1,NULL,NULL)")
    con.execute(
        "INSERT INTO proposals (id, title, source_intel_id, status, decision_reason) "
        "VALUES ('37cb4ce26a9b','Adopt AgentForge orchestration patterns','intel-1',"
        "?, 'fabricated, verify()=1.00 pre-wq2027')", (status,))
    con.commit()
    con.close()


def run_sweep(db_path):
    out = subprocess.run([sys.executable, SWEEP, "--db", db_path],
                          capture_output=True, text=True)
    return out.stdout


if __name__ == "__main__":
    failures = 0

    with tempfile.NamedTemporaryFile(dir=HERE, suffix=".db", delete=False) as f:
        db_accept = f.name
    seed(db_accept, "accept")
    out = run_sweep(db_accept)
    os.remove(db_accept)
    caught = "1 pending proposal" in out
    print(f"{'ok ' if caught else 'BUG'}  status='accept' (what save_proposal() actually "
          f"writes for an adopted-but-fabricated proposal) -> sweep reports: "
          f"{out.splitlines()[0]!r}")
    if not caught:
        failures += 1

    with tempfile.NamedTemporaryFile(dir=HERE, suffix=".db", delete=False) as f:
        db_pending = f.name
    seed(db_pending, "pending")
    out = run_sweep(db_pending)
    os.remove(db_pending)
    caught = "1 pending proposal" in out
    print(f"{'ok ' if caught else 'BUG'}  status='pending' (literal, never actually "
          f"written by save_proposal()) -> sweep reports: {out.splitlines()[0]!r}")
    if not caught:
        failures += 1

    print()
    print(f"{failures}/2 cases the sweep script fails to catch")
