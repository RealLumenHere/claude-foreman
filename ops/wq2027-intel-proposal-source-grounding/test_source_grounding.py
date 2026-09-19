#!/usr/bin/env python3
"""wq#2027 — regression tests for the intel->proposal source-grounding gate.

Reference defect (foreman/research/AGENTFORGE_EVAL.md section 0/1): proposals
ff80d5e94515 (Saichandra2520/AgentForge) and 37cb4ce26a9b (Roshanrameshhub/
AgentForge) were generated from a repo TITLE only — classified_intel.url and
.summary were both empty — and scored verify()=1.00 because every existing
layer checks a claim against Lumen CONTEXT, never against the external
subject. Two independent fixes, tested here:

  1. minerva_proposal.get_candidates() — a classified_intel row with an empty
     url or summary is not eligible to seed a proposal at all.
  2. minerva_verify.source_grounding_check() / verify(source_url=...,
     subject_claim_text=...) — even a row that DOES have a url/summary blocks
     if the subject 404s, or if the proposal's claim about it isn't supported
     by the fetched summary.

Loads both patched files directly via importlib so this never touches
/home/lumen/minerva or the network for anything except the two cases that
exercise subject_exists() over a stubbed requests.get (no real HTTP call is
made; requests.get is monkeypatched in-process).

    python3 ops/wq2027-intel-proposal-source-grounding/test_source_grounding.py

Exits 0 when every case passes, 1 otherwise.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY_PATH = os.path.join(HERE, "..", "wq1997-external-advisory-check", "minerva_verify.py")
PROPOSAL_PATH = os.path.join(HERE, "..", "wq2019-proposal-reviewer-fix", "minerva_proposal.py")

# --- load minerva_verify.py standalone (same pattern as test_external_advisory_check.py) ---
_v_spec = importlib.util.spec_from_file_location("minerva_verify", VERIFY_PATH)
mv = importlib.util.module_from_spec(_v_spec)
_v_spec.loader.exec_module(mv)

_SCRATCH = Path(tempfile.mkdtemp(prefix="wq2027_verify_"))
mv.DB = _SCRATCH / "minerva.db"
mv.LOG = _SCRATCH / "verify.log"
_conn = sqlite3.connect(mv.DB)
_conn.execute("""CREATE TABLE verification_log (
    subject TEXT, input_text TEXT, context_ref TEXT, citation_score REAL,
    judge_score REAL, final_score REAL, passed INTEGER, rejected_reason TEXT,
    model TEXT, ts TEXT, judge_rationale TEXT, error_kind TEXT, mode TEXT,
    n_claims INTEGER, claims_entailed INTEGER, span_confidence TEXT)""")
_conn.commit(); _conn.close()

# minerva_proposal.py does `from minerva_verify import verify` at module level.
# Publish our already-loaded module under that exact name so the import
# resolves from sys.modules and never touches /home/lumen/minerva on disk.
sys.modules["minerva_verify"] = mv

_p_spec = importlib.util.spec_from_file_location("minerva_proposal_wq2027", PROPOSAL_PATH)
mp = importlib.util.module_from_spec(_p_spec)
_p_spec.loader.exec_module(mp)

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def _mk_intel_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE classified_intel (
        id TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT,
        audience_relevance REAL, self_relevance REAL, category TEXT,
        reasoning TEXT, ts TEXT, processed INTEGER DEFAULT 0)""")
    return conn


def main():
    # ------------------------------------------------------------------
    # Part 1: minerva_proposal.get_candidates() eligibility gate
    # ------------------------------------------------------------------
    conn = _mk_intel_db()
    conn.executemany(
        "INSERT INTO classified_intel (id, source, title, url, audience_relevance, "
        "self_relevance, category, reasoning, ts, processed) VALUES (?,?,?,?,?,?,?,?,?,0)",
        [
            # the exact defect shape: title-only, no url, no summary (added below)
            ("ff80d5e9", "github", "Saichandra2520/AgentForge", "", 0.8, 0.8, "repo", "HINT: scaffolder", "t"),
            ("37cb4ce2", "github", "Roshanrameshhub/AgentForge", "https://github.com/Roshanrameshhub/AgentForge",
             0.8, 0.8, "repo", "HINT: orchestration", "t"),
            ("eligible1", "github", "DataBassGit/AgentForge", "https://github.com/DataBassGit/AgentForge",
             0.9, 0.9, "repo", "HINT: real framework", "t"),
        ],
    )
    # _ensure_intel_source_columns() must additively create `summary` (older
    # schema, ops/wq1500-minerva-promoter/test_minerva_promote.py, has no such
    # column at all).
    cols_before = {r[1] for r in conn.execute("PRAGMA table_info(classified_intel)")}
    check("summary column absent before migration", "summary" not in cols_before)
    mp._ensure_intel_source_columns(conn)
    cols_after = {r[1] for r in conn.execute("PRAGMA table_info(classified_intel)")}
    check("_ensure_intel_source_columns() adds `summary` additively", "summary" in cols_after)

    # backfill summary: 37cb4ce2 has a url but (as in the real defect) still
    # no fetched summary -> still ineligible. eligible1 has both.
    conn.execute("UPDATE classified_intel SET summary = ? WHERE id = ?",
                 ("847 stars, 1144 commits, GPL-3.0, low-code agent framework.", "eligible1"))
    conn.commit()

    mp.DB = ":memory:"  # get_candidates() opens sqlite3.connect(DB) itself; override to reuse conn instead
    # get_candidates() connects via sqlite3.connect(mp.DB) internally, so point
    # it at a file-backed temp db carrying the rows just inserted.
    tmp_db = _SCRATCH / "proposal_intel.db"
    conn.commit()
    with sqlite3.connect(tmp_db) as fconn:
        conn.backup(fconn)
    mp.DB = tmp_db

    rows = mp.get_candidates(min_self=0.6, limit=10)
    ids = {r[0] for r in rows}
    check("empty-url/empty-summary row (ff80d5e9, the April proposal's source) is excluded",
          "ff80d5e9" not in ids, ids)
    check("url-only/empty-summary row (37cb4ce2, the August proposal's source) is excluded",
          "37cb4ce2" not in ids, ids)
    check("row with both url and summary is eligible",
          "eligible1" in ids, ids)
    check("get_candidates() returns 9 columns including url, summary",
          len(rows[0]) == 9, rows[0] if rows else None)

    # ------------------------------------------------------------------
    # Part 2: minerva_verify.subject_exists() / source_grounding_check()
    # ------------------------------------------------------------------
    class _Resp:
        def __init__(self, status_code):
            self.status_code = status_code

    _real_get = mv.requests.get

    def _fake_get_404(*a, **k):
        return _Resp(404)

    def _fake_get_200(*a, **k):
        return _Resp(200)

    def _fake_get_err(*a, **k):
        raise ConnectionError("simulated outage")

    mv.requests.get = _fake_get_404
    check("subject_exists() is False on a definitive 404",
          mv.subject_exists("https://github.com/nope/doesnotexist") is False)

    mv.requests.get = _fake_get_200
    check("subject_exists() is True on 200",
          mv.subject_exists("https://github.com/DataBassGit/AgentForge") is True)

    mv.requests.get = _fake_get_err
    check("subject_exists() is None (fail-closed 'not confirmed') on a network error",
          mv.subject_exists("https://github.com/DataBassGit/AgentForge") is None)

    check("subject_exists() is None for a non-GitHub url (no repo slug to check)",
          mv.subject_exists("https://example.com/whatever") is None)

    mv.requests.get = _real_get

    # source_grounding_check: no url/summary at all -> blocked, distinct reason
    sg = mv.source_grounding_check("claims text", "", "")
    check("source_grounding_check() blocks when url/summary are both empty",
          sg["checked"] is False and sg["error"] is True and sg["reason"] == "no_source", sg)

    # Stub out the expensive legs (embeddings/GitHub) so the rest of the test
    # proves the *wiring*, not local-model/network availability — same
    # technique test_external_advisory_check.py uses for Layer 0.
    mv.subject_exists = lambda url: True
    mv.semantic_citation_score = lambda claims, context: (
        1.0, [(c, 1.0, "ok") for c in claims], None)
    sg = mv.source_grounding_check(
        "AgentForge has a self-improvement loop and a skill library.",
        "https://github.com/DataBassGit/AgentForge", "A low-code agent framework.")
    check("source_grounding_check() passes when subject exists and claims are grounded",
          sg["checked"] is True and sg["exists"] is True and sg["unsupported_claims"] == [], sg)

    mv.semantic_citation_score = lambda claims, context: (
        0.0, [(c, 0.0, "low_similarity") for c in claims], None)
    sg = mv.source_grounding_check(
        "AgentForge has a self-improvement loop and a skill library.",
        "https://github.com/DataBassGit/AgentForge", "A low-code agent framework.")
    check("source_grounding_check() flags claims the fetched summary never supports",
          sg["unsupported_claims"] != [], sg)

    mv.subject_exists = lambda url: False
    sg = mv.source_grounding_check("claim", "https://github.com/x/doesnotexist", "some summary")
    check("source_grounding_check() reports exists=False for a 404 subject",
          sg["exists"] is False, sg)

    # ------------------------------------------------------------------
    # Part 3: verify() wiring — the exact ff80d5e94515/37cb4ce26a9b shape:
    # citation=1.0/judge=1.0 against Lumen CONTEXT (every prior layer passes)
    # while the claim about the EXTERNAL subject is fabricated.
    # ------------------------------------------------------------------
    mv.semantic_citation_score = lambda claims, context: (
        1.0, [(c, 1.0, "ok") for c in claims], None)
    mv.judge_entailment_uq = lambda claims, context: (
        1.0, ["SUPPORTED"] * len(claims), "", None, [1.0] * len(claims))

    # backward compatibility: no source_url/subject_claim_text passed -> the
    # new layer must not activate at all (every pre-existing call site).
    result = mv.verify("Lumen already runs LangGraph 1.2.5.", "Lumen already runs LangGraph 1.2.5.",
                        subject="wq2027_no_opt_in", mode="semantic")
    check("verify() without source params is unaffected (backward compatible)",
          result["passed"] is True and result["source_grounding"] is None, result)

    # opted in, subject 404s (the AGENTFORGE_EVAL Roshanrameshhub row does
    # resolve, but this proves the block for a subject that genuinely doesn't)
    mv.subject_exists = lambda url: False
    result = mv.verify(
        "Lumen already runs LangGraph 1.2.5.", "Lumen already runs LangGraph 1.2.5.",
        subject="wq2027_404_subject", mode="semantic",
        source_url="https://github.com/nope/doesnotexist", source_summary="n/a",
        subject_claim_text="This repo has a self-improvement loop.")
    check("verify() blocks when the external subject does not exist, despite citation=judge=1.0",
          result["passed"] is False and "subject_not_found" in (result["reason"] or ""), result)

    # opted in, subject exists, but the claim about it is not supported by the
    # fetched summary — the exact ff80d5e94515/37cb4ce26a9b defect shape.
    mv.subject_exists = lambda url: True
    mv.semantic_citation_score = lambda claims, context: (
        1.0, [(c, 1.0, "ok") for c in claims], None) if context == "Lumen already runs LangGraph 1.2.5." \
        else (0.0, [(c, 0.0, "low_similarity") for c in claims], None)
    result = mv.verify(
        "Lumen already runs LangGraph 1.2.5.", "Lumen already runs LangGraph 1.2.5.",
        subject="wq2027_unsupported_claim", mode="semantic",
        source_url="https://github.com/Roshanrameshhub/AgentForge",
        source_summary="LangGraph chat demo, 6 commits, MIT, no memory/skill/self-improvement code.",
        subject_claim_text="This repo has a self-improvement loop, a skill library, and long-term memory.")
    check("verify() blocks an unsupported external-subject claim despite citation=judge=1.0 "
          "against Lumen CONTEXT (the ff80d5e94515/37cb4ce26a9b defect shape)",
          result["passed"] is False and "subject_unsupported" in (result["reason"] or ""), result)

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s): {FAILURES}")
        sys.exit(1)
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
