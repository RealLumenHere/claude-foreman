#!/usr/bin/env python3
"""End-to-end proof of the wq#1997 external-existence check.

Reproduces the wq#1512 hallucination directly: minerva_proposal 92c6cef468e9
cited CVE-2026-83601 for netdata; that id resolves nowhere on NVD. Exercises
the real network calls (NVD + GitHub advisory API) against fabricated and
real identifiers. Does not import minerva_verify's module-level DB/LiteLLM
paths as live state — loads the file directly via importlib so it never
touches /home/lumen/minerva.

    python3 ops/wq1997-external-advisory-check/test_external_advisory_check.py

Exits 0 when every case passes, 1 otherwise. Requires outbound network
access to nvd.nist.gov and api.github.com.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(HERE, "minerva_verify.py")

spec = importlib.util.spec_from_file_location("minerva_verify_wq1997", MODULE_PATH)
mv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mv)

# Scratch DB/log paths so verify() never touches the live /home/lumen/minerva
# state, which this worktree cannot even reach.
_SCRATCH = Path(tempfile.mkdtemp(prefix="wq1997_verify_"))
mv.DB = _SCRATCH / "minerva.db"
mv.LOG = _SCRATCH / "verify.log"
_conn = sqlite3.connect(mv.DB)
_conn.execute("""CREATE TABLE verification_log (
    subject TEXT, input_text TEXT, context_ref TEXT, citation_score REAL,
    judge_score REAL, final_score REAL, passed INTEGER, rejected_reason TEXT,
    model TEXT, ts TEXT, judge_rationale TEXT, error_kind TEXT, mode TEXT,
    n_claims INTEGER, claims_entailed INTEGER, span_confidence TEXT)""")
_conn.commit(); _conn.close()

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def main():
    # wq#1512's exact fabricated CVE must come back unresolved.
    r = mv.external_advisory_check(
        "minerva_proposal cites CVE-2026-83601 as a MEDIUM netdata advisory."
    )
    check("wq1512 fabricated CVE is flagged unresolved",
          r["unresolved"] == ["CVE-2026-83601"], r)

    # A real, long-standing CVE must resolve and not block.
    r = mv.external_advisory_check("libuv is affected by CVE-2024-32019.")
    check("real CVE resolves (not unresolved)",
          r["checked"] == ["CVE-2024-32019"] and r["unresolved"] == [], r)

    # Text with no CVE/GHSA id is a no-op — zero HTTP calls, zero findings.
    r = mv.external_advisory_check("Netdata has no known issues right now.")
    check("no ids present -> no-op", r == {"checked": [], "unresolved": [], "error": False}, r)

    # Layer 0 must be wired into verify(): patch out the citation/judge legs
    # (the actual wq#1512 failure mode — both scored 1.0 for a claim that
    # faithfully echoed a hallucinated source) so verify() proves the new
    # layer, not local-model availability.
    mv.semantic_citation_score = lambda claims, context: (1.0, [(c, 1.0, "ok") for c in claims], None)
    mv.judge_entailment_uq = lambda claims, context: (1.0, ["SUPPORTED"] * len(claims), "", None, [1.0] * len(claims))

    claim = "netdata carries CVE-2026-83601, a MEDIUM severity advisory."
    result = mv.verify(claim, claim, subject="wq1997_selftest", mode="semantic")
    check("verify() rejects a fabricated CVE despite citation=1.0/judge=1.0",
          result["passed"] is False and result["citation_score"] == 1.0 and result["judge_score"] == 1.0
          and "CVE-2026-83601" in result["unresolved_advisories"],
          result)

    # And the control: a claim with no advisory id at all still passes on
    # citation/judge alone — Layer 0 must be a no-op, not a blanket reject.
    clean = "Netdata's collector runs as a systemd unit with no known issues."
    result2 = mv.verify(clean, clean, subject="wq1997_selftest_clean", mode="semantic")
    check("verify() still passes a claim with no advisory id",
          result2["passed"] is True and result2["unresolved_advisories"] == [],
          result2)

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s): {FAILURES}")
        sys.exit(1)
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
