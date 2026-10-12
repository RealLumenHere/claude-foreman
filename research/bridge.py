#!/usr/bin/env python3
"""bridge — research proposal → wq bridge (design/wq3755 §4).

wq#3759 lands step 1 only, the evidence check, so the gate has a consumer that can refuse
its output. wq#3760 adds the rejection ledger, the weekly cap and the `wq add --frank-gated`
submit; until then this is dry-run only and never writes to wq.

A proposal is refused unless its evidence file exists, carries every schema field with the
right type, came from an accepted backend on the proposal's arch, has install_exit == 0 and
smoke_exit == 0, re-hashes to the proposal's current bytes, and is younger than 7 days.

Usage:
  bridge.py --dry-run <proposal.json> [--evidence-dir D] [--accept-backend B ...]
Exit 0 = would submit, 1 = refused (reasons printed as JSON).
"""
import argparse
import datetime
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sandbox_gate import EVIDENCE_DIR, EVIDENCE_FIELDS  # noqa: E402

MAX_AGE = datetime.timedelta(days=7)
ACCEPTED_BACKENDS = ("container",)


def refusals(proposal_path, evidence_dir=EVIDENCE_DIR, accepted=ACCEPTED_BACKENDS, now=None):
    """Every reason this proposal can't go to Frank; empty list means it can."""
    with open(proposal_path, "rb") as f:
        raw = f.read()
    try:
        prop = json.loads(raw)
        pid, arch = prop["id"], prop["arch"]
    except (ValueError, KeyError, TypeError):
        return ["proposal is not a JSON object with id and arch"]
    path = os.path.join(evidence_dir, f"{os.path.basename(str(pid))}.json")
    if not os.path.isfile(path):
        return [f"no evidence: {path}"]
    try:
        with open(path) as f:
            ev = json.load(f)
    except ValueError as e:
        return [f"evidence is not JSON: {e}"]
    reasons = [f"evidence field {k} missing or not {getattr(t, '__name__', t)}"
               for k, t in EVIDENCE_FIELDS.items()
               if k not in ev or isinstance(ev[k], bool) or not isinstance(ev[k], t)]
    if reasons:
        return reasons
    if ev.get("backend") not in accepted:
        reasons.append(f"backend {ev.get('backend')!r} not in {list(accepted)}")
    if ev["arch"] != arch:
        reasons.append(f"evidence arch {ev['arch']} != proposal arch {arch}")
    if ev["install_exit"] != 0:
        reasons.append(f"install_exit={ev['install_exit']}")
    if ev["smoke_exit"] != 0:
        reasons.append(f"smoke_exit={ev['smoke_exit']}")
    if ev["proposal_sha256"] != hashlib.sha256(raw).hexdigest():
        reasons.append("proposal_sha256 mismatch: proposal edited after gating")
    try:
        ts = datetime.datetime.strptime(ev["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
        if (now or datetime.datetime.now(datetime.timezone.utc)) - ts > MAX_AGE:
            reasons.append(f"evidence stale: ts {ev['ts']} older than {MAX_AGE.days} days")
    except (KeyError, TypeError, ValueError):
        reasons.append("evidence ts missing or unparseable")
    return reasons


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", required=True,
                    help="required until wq#3760 lands the submit path")
    ap.add_argument("proposal")
    ap.add_argument("--evidence-dir", default=EVIDENCE_DIR)
    ap.add_argument("--accept-backend", action="append",
                    help=f"override accepted backends (default {list(ACCEPTED_BACKENDS)})")
    a = ap.parse_args(argv)
    reasons = refusals(a.proposal, a.evidence_dir, tuple(a.accept_backend or ACCEPTED_BACKENDS))
    print(json.dumps({"proposal": a.proposal, "verdict": "refused" if reasons else "would_submit",
                      "reasons": reasons}))
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
