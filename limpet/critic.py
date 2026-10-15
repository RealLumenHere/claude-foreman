"""wq#3966 -- adversarial critic pass in the Limpet analysis loop.

Split from wq#3944/A3 (design: ``design/wq3929_competitive_analysis.md`` §5 A3,
gap G3). REA-Rust and VRAX both run a propose -> review -> adjudicate loop
instead of a single agent proposing and committing a finding unchecked; that
topology was *described*, not verified, when wq#3929 ran (search down,
ISSUE-LOG #1325) and `fleet-search web` is still down as of 2026-10-15 (same
outage, no new prior art). The pattern this module adopts does not depend on
either project's internals -- it is the fleet's own house propose/critic/
adjudicate posture applied to one RE finding.

The three-stage loop:

  propose    the agent (or operator) drafts a finding -- not this module's
             job; a finding is just a dict (see below) until it is reviewed.
  critic     a second model call, asked to *falsify* the finding against the
             workspace evidence log (a distinct prompt from the one that
             proposed it -- may be the same local model). Limpet's agent core
             is still design-only (``design/wq3806_limpet_architecture.md``
             §6: model routing via LiteLLM is not yet built), so this module
             does not place that call itself. The caller (the agent loop, once
             it exists) gets the model's verdict however it likes and passes
             it to ``review()`` as ``critic_verdict``/``critic_rationale``.
             This keeps the gate logic here pure and offline-testable instead
             of speculatively wiring a model client that does not exist yet.
  adjudicate ``review()``'s job. A deterministic structural gate runs first
             and can reject a finding before any model call would matter:
             every finding must cite >=1 real evidence entry, and a finding
             may not name a currently-*open* ledger obligation (wq#3965) in
             its optional ``closes`` list -- closing an obligation is a
             deliberate act with its own rationale+evidence
             (``obligations.resolve``/``waive``), never a side effect of a
             finding being accepted. Past that gate, the critic's verdict
             decides: "supported" accepts the finding; "falsified" does not
             silently drop it -- a ledger obligation records the disagreement
             instead, so it is tracked and must be resolved or waived later.

Finding shape (matches ``workspace_report._collect_findings``'s
``evidence/findings/*.json``, plus one new optional field):

  {"id": "F-001", "title": "...", "severity": "high", "summary": "...",
   "evidence": [{"path": "evidence/x", "sha256": "...", "locator": "L1"},
                "ev_<64 hex>", "evidence/y#L3"],
   "closes": ["OB-003"]}   # optional: obligation ids this finding claims to answer

``evidence`` entries may be a dict with a ``path``/``ref``/``id`` key, or a
bare string (REA ``ev_`` id, or a workspace-relative path with an optional
``#locator``) -- the same vocabulary ``obligations.py`` already accepts.
"""
from __future__ import annotations

import obligations

VERDICT_ACCEPTED = "accepted"
VERDICT_REJECTED = "rejected"
VERDICT_CONTESTED = "contested"

CRITIC_SUPPORTED = "supported"
CRITIC_FALSIFIED = "falsified"
CRITIC_VERDICTS = (CRITIC_SUPPORTED, CRITIC_FALSIFIED)


class CriticError(Exception):
    """A malformed finding or critic input the review step refuses to process."""


def _evidence_ref(entry) -> str:
    """Return one finding evidence entry as a bare ref string, or raise."""
    if isinstance(entry, dict):
        ref = entry.get("path") or entry.get("ref") or entry.get("id")
        locator = entry.get("locator")
        if ref and locator and "#" not in str(ref):
            ref = f"{ref}#{locator}"
    else:
        ref = entry
    if not ref or not isinstance(ref, str) or not ref.strip():
        raise CriticError(f"unusable evidence entry: {entry!r}")
    return ref.strip()


def deterministic_check(workspace: str, finding: dict) -> dict:
    """The structural half of the critic step -- no model call involved.

    Returns ``{"ok": bool, "reasons": [...], "evidence_refs": [...]}``.
    ``reasons`` is empty iff ``ok``. ``evidence_refs`` are the finding's
    evidence entries normalized to the ref vocabulary ``obligations.py``
    accepts, so a caller opening a contested-finding obligation can reuse
    them without re-parsing the finding.
    """
    if not isinstance(finding, dict):
        raise CriticError("finding must be an object")

    reasons = []
    evidence = finding.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        reasons.append("finding cites no evidence entry")
        evidence = []

    refs = []
    for entry in evidence:
        try:
            refs.append(_evidence_ref(entry))
        except CriticError as exc:
            reasons.append(str(exc))

    closes = finding.get("closes") or []
    if closes:
        ledger = obligations.load(workspace)
        open_ids = {o["id"] for o in ledger["obligations"] if o.get("status") == "open"}
        blocked = sorted(ob_id for ob_id in closes if ob_id in open_ids)
        if blocked:
            reasons.append(
                "finding claims to close open obligation(s) "
                f"{', '.join(blocked)} directly; resolve or waive them explicitly instead"
            )

    return {"ok": not reasons, "reasons": reasons, "evidence_refs": refs}


def review(workspace: str, finding: dict, *, critic_verdict: str, critic_rationale: str,
          by: str = "critic", commit: bool = True) -> dict:
    """Run the full critic step: the deterministic gate, then the model's verdict.

    A finding that fails the deterministic gate is rejected outright -- the
    critic model is never consulted, matching "DETERMINISTIC check" in the
    ticket (a model call cannot un-fail a structural defect). Past the gate,
    ``critic_verdict`` (the second model call's result, obtained by the
    caller) decides the outcome:

      "supported"  -> accepted, no ledger change.
      "falsified"  -> contested: never silently dropped. A ledger obligation
                      (wq#3965) recording the disagreement is opened instead,
                      citing the same evidence the finding cited, so the
                      disagreement is tracked and must be resolved or waived
                      like any other open RE question.

    Returns ``{"verdict": accepted|rejected|contested, "reasons": [...],
    "obligation": {...} or None}``.
    """
    det = deterministic_check(workspace, finding)
    if not det["ok"]:
        return {"verdict": VERDICT_REJECTED, "reasons": det["reasons"], "obligation": None}

    if critic_verdict not in CRITIC_VERDICTS:
        raise CriticError(f"critic_verdict must be one of {CRITIC_VERDICTS}")
    if not critic_rationale or not critic_rationale.strip():
        raise CriticError("a critic verdict needs a rationale")

    if critic_verdict == CRITIC_SUPPORTED:
        return {"verdict": VERDICT_ACCEPTED, "reasons": [], "obligation": None}

    fid = finding.get("id") or "finding"
    severity = finding.get("severity")
    if severity not in obligations.SEVERITIES:
        severity = "medium"
    claim = f"Critic contests {fid}: {critic_rationale.strip()}"
    ob = obligations.open_obligation(
        workspace, claim, opened_by="critic", evidence=det["evidence_refs"],
        severity=severity, by=by, commit=commit,
    )
    return {"verdict": VERDICT_CONTESTED, "reasons": [critic_rationale.strip()], "obligation": ob}
