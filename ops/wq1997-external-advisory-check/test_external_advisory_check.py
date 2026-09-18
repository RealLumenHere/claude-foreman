#!/usr/bin/env python3
"""End-to-end proof of the wq#1997 external-existence check, as corrected by
wq#2005.

CVE-2026-83601 / GHSA-3qh4-842w-fvrm — the id wq#1512 and wq#1997 both called
fabricated — is a real netdata advisory, published at repo level 2026-09-02
and not yet synced to NVD or to GitHub's global reviewed DB. So these cases
pin both directions: the real-but-fresh id must resolve repo-scoped and pass,
while an id that resolves nowhere at all must still block. Exercises the real
network calls (NVD + GitHub global and repo advisory APIs). Does not import
minerva_verify's module-level DB/LiteLLM
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
    # wq#2005 / wq#2001's contradiction: CVE-2026-83601 is NOT fabricated. It
    # is a real netdata advisory published 2026-09-02 at repo level, still
    # absent from NVD and from GitHub's global reviewed DB. The repo-scoped
    # lookup must find it and must not block it. (wq#1997's version of this
    # case asserted the opposite and was wrong on its own reference id.)
    r = mv.external_advisory_check(
        "minerva_proposal cites CVE-2026-83601 as a MEDIUM netdata advisory."
    )
    check("wq2005 fresh real CVE is not blocked",
          r["unresolved"] == [], r)
    check("wq2005 fresh real CVE resolves via repo-scoped lookup",
          r["inconclusive"] == [], r)

    # Same advisory by its GHSA id, from a github.com URL rather than a bare
    # project word — the other repo-inference path.
    r = mv.external_advisory_check(
        "See https://github.com/netdata/netdata/security/advisories for GHSA-3qh4-842w-fvrm."
    )
    check("fresh real GHSA resolves from a github.com URL",
          r["unresolved"] == [] and r["inconclusive"] == [], r)

    # An id that resolves NOWHERE — not NVD, not the global DB, not the repo
    # list we actually read — is still a hard block. This is the wq#1512
    # hallucination shape the Layer 0 exists for.
    r = mv.external_advisory_check(
        "minerva_proposal cites CVE-2026-99999 as a HIGH netdata advisory."
    )
    check("fabricated CVE still blocks (repo list read, id absent)",
          r["unresolved"] == ["CVE-2026-99999"], r)

    # No repo is inferable, so the source that would have known was never
    # asked: inconclusive (route to review), never a silent auto-reject.
    r = mv.external_advisory_check("CVE-2026-99998 has critical severity.")
    check("unresolvable id with no repo candidate is inconclusive, not blocked",
          r["unresolved"] == [] and r["inconclusive"] == ["CVE-2026-99998"], r)

    # A real, long-standing CVE must resolve and not block.
    r = mv.external_advisory_check("libuv is affected by CVE-2024-32019.")
    check("real CVE resolves (not unresolved)",
          r["checked"] == ["CVE-2024-32019"] and r["unresolved"] == []
          and r["inconclusive"] == [], r)

    # Text with no CVE/GHSA id is a no-op — zero HTTP calls, zero findings.
    r = mv.external_advisory_check("Netdata has no known issues right now.")
    check("no ids present -> no-op",
          r == {"checked": [], "unresolved": [], "inconclusive": [], "error": False}, r)

    # Repo inference, without network: every tier, and the cap.
    cands = mv.repo_candidates("github.com/netdata/netdata flagged CVE-2026-83601")
    check("repo_candidates prefers the explicit github.com slug",
          cands[0] == "netdata/netdata", cands)
    check("repo_candidates guesses name/name for a bare project word",
          "netdata/netdata" in mv.repo_candidates("a netdata advisory"),
          mv.repo_candidates("a netdata advisory"))
    check("repo_candidates drops boilerplate words",
          mv.repo_candidates("the advisory has critical severity") == [],
          mv.repo_candidates("the advisory has critical severity"))
    check("repo_candidates is capped",
          len(mv.repo_candidates(" ".join(f"proj{i}" for i in range(20))))
          <= mv.ADVISORY_REPO_MAX_CANDIDATES)

    # Layer 0 must be wired into verify(): patch out the citation/judge legs
    # (the actual wq#1512 failure mode — both scored 1.0 for a claim that
    # faithfully echoed a hallucinated source) so verify() proves the new
    # layer, not local-model availability.
    mv.semantic_citation_score = lambda claims, context: (1.0, [(c, 1.0, "ok") for c in claims], None)
    mv.judge_entailment_uq = lambda claims, context: (1.0, ["SUPPORTED"] * len(claims), "", None, [1.0] * len(claims))

    claim = "netdata carries CVE-2026-99999, a HIGH severity advisory."
    result = mv.verify(claim, claim, subject="wq1997_selftest", mode="semantic")
    check("verify() rejects a fabricated CVE despite citation=1.0/judge=1.0",
          result["passed"] is False and result["citation_score"] == 1.0 and result["judge_score"] == 1.0
          and "CVE-2026-99999" in result["unresolved_advisories"],
          result)

    # wq#2005 liveness: the real, fresh advisory must NOT come back as a hard
    # block. It resolves repo-scoped here, so verify() passes it outright.
    live = "netdata carries CVE-2026-83601, a MEDIUM severity advisory."
    result = mv.verify(live, live, subject="wq2005_fresh_real", mode="semantic")
    check("verify() does not auto-reject the real fresh CVE-2026-83601",
          result["passed"] is True and result["unresolved_advisories"] == [],
          result)

    # And an id nothing could settle: flagged for review, `passed` untouched.
    murky = "CVE-2026-99998 has critical severity."
    result = mv.verify(murky, murky, subject="wq2005_inconclusive", mode="semantic")
    check("verify() flags an unsettled id for review instead of rejecting",
          result["passed"] is True and result["advisory_review_required"] is True
          and result["inconclusive_advisories"] == ["CVE-2026-99998"]
          and "advisory_needs_review" in (result["reason"] or ""),
          result)

    # And the control: a claim with no advisory id at all still passes on
    # citation/judge alone — Layer 0 must be a no-op, not a blanket reject.
    clean = "Netdata's collector runs as a systemd unit with no known issues."
    result2 = mv.verify(clean, clean, subject="wq1997_selftest_clean", mode="semantic")
    check("verify() still passes a claim with no advisory id",
          result2["passed"] is True and result2["unresolved_advisories"] == []
          and result2["advisory_review_required"] is False,
          result2)

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s): {FAILURES}")
        sys.exit(1)
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
