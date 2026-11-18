#!/usr/bin/env python3
"""Skill admission pipeline — quarantine → scan → approve → promote.

Implements the full supply-chain defense pipeline per wq#4924:
  1. Quarantine: copy skill to isolated staging area
  2. Record: publisher, repo, release, SHA-256 manifest
  3. Inspect: static analysis via scan_skill
  4. Capability audit: compare declared vs actual
  5. Evidence-backed approval record
  6. Promote: immutable versioned copy to registry

Sources referenced:
  - Snyk ToxicSkills (Feb 2026): https://snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub/
  - OWASP AST-01: https://owasp.org/www-project-agentic-skills-top-10/ast01
  - Unit 42 OpenClaw analysis: https://unit42.paloaltonetworks.com/openclaw-ai-supply-chain-risk/

Usage:
  python3 admit_skill.py quarantine /path/to/skill --source clawhub --publisher user/repo
  python3 admit_skill.py scan <quarantine-id>
  python3 admit_skill.py approve <quarantine-id> --reviewer <name> --notes "..."
  python3 admit_skill.py promote <quarantine-id>
  python3 admit_skill.py audit-existing /path/to/skills-root
  python3 admit_skill.py status [quarantine-id]
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PIPELINE_ROOT = Path(os.environ.get(
    "SKILL_PIPELINE_ROOT",
    "/home/lumen/foreman/security/skill-pipeline"
))

QUARANTINE_DIR = PIPELINE_ROOT / "quarantine"
REGISTRY_DIR = PIPELINE_ROOT / "registry"
RECORDS_DIR = PIPELINE_ROOT / "records"
AUDIT_LOG = PIPELINE_ROOT / "audit.jsonl"


@dataclass
class AdmissionRecord:
    quarantine_id: str
    skill_name: str
    source: str  # clawhub, local, git
    publisher: str
    repo_url: str = ""
    release: str = ""
    quarantine_time: str = ""
    scan_time: str = ""
    scan_verdict: str = ""
    scan_findings_count: int = 0
    scan_critical_count: int = 0
    scan_high_count: int = 0
    sha256_manifest: dict = field(default_factory=dict)
    approval_time: str = ""
    reviewer: str = ""
    approval_notes: str = ""
    promote_time: str = ""
    status: str = "quarantined"  # quarantined, scanned, approved, promoted, rejected

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path) -> "AdmissionRecord":
        data = json.loads(path.read_text())
        return cls(**data)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit_append(event: dict):
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    event["timestamp"] = _now_iso()
    with open(AUDIT_LOG, "a") as f:
        f.write(json.dumps(event) + "\n")


def _gen_quarantine_id(skill_name: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    h = hashlib.sha256(f"{skill_name}{ts}{os.getpid()}".encode()).hexdigest()[:8]
    return f"q-{skill_name}-{ts}-{h}"


def cmd_quarantine(args):
    skill_path = Path(args.skill_path).resolve()
    if not skill_path.is_dir():
        print(f"ERROR: {skill_path} is not a directory", file=sys.stderr)
        sys.exit(1)

    skill_name = skill_path.name
    qid = _gen_quarantine_id(skill_name)
    q_dest = QUARANTINE_DIR / qid

    q_dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_path, q_dest / "skill", dirs_exist_ok=True)

    record = AdmissionRecord(
        quarantine_id=qid,
        skill_name=skill_name,
        source=args.source,
        publisher=args.publisher or "",
        repo_url=args.repo_url or "",
        release=args.release or "",
        quarantine_time=_now_iso(),
    )

    from scan_skill import compute_manifest
    record.sha256_manifest = compute_manifest(q_dest / "skill")

    record_path = RECORDS_DIR / f"{qid}.json"
    record.save(record_path)

    _audit_append({
        "event": "quarantine",
        "quarantine_id": qid,
        "skill_name": skill_name,
        "source": args.source,
        "publisher": args.publisher or "",
        "file_count": len(record.sha256_manifest),
    })

    print(f"Quarantined: {qid}")
    print(f"  Path: {q_dest / 'skill'}")
    print(f"  Record: {record_path}")
    print(f"  Files: {len(record.sha256_manifest)}")
    return qid


def cmd_scan(args):
    qid = args.quarantine_id
    record_path = RECORDS_DIR / f"{qid}.json"
    if not record_path.exists():
        print(f"ERROR: No record for {qid}", file=sys.stderr)
        sys.exit(1)

    record = AdmissionRecord.load(record_path)
    skill_dir = QUARANTINE_DIR / qid / "skill"

    sys.path.insert(0, str(Path(__file__).parent))
    from scan_skill import scan_skill

    result = scan_skill(skill_dir, strict=args.strict)

    record.scan_time = _now_iso()
    record.scan_verdict = result.verdict
    record.scan_findings_count = len(result.findings)
    record.scan_critical_count = result.critical_count
    record.scan_high_count = result.high_count
    record.status = "scanned" if result.verdict != "FAIL" else "rejected"

    scan_report_path = RECORDS_DIR / f"{qid}.scan.json"
    scan_report_path.write_text(json.dumps({
        "quarantine_id": qid,
        "verdict": result.verdict,
        "findings": [asdict(f) for f in result.findings],
        "sha256_manifest": result.sha256_manifest,
    }, indent=2))

    record.save(record_path)

    _audit_append({
        "event": "scan",
        "quarantine_id": qid,
        "verdict": result.verdict,
        "findings_count": len(result.findings),
        "critical": result.critical_count,
        "high": result.high_count,
    })

    from scan_skill import format_text
    print(format_text(result))

    if record.status == "rejected":
        print(f"\nREJECTED: {qid} — {result.critical_count} critical findings")
    return result.verdict


def cmd_approve(args):
    qid = args.quarantine_id
    record_path = RECORDS_DIR / f"{qid}.json"
    if not record_path.exists():
        print(f"ERROR: No record for {qid}", file=sys.stderr)
        sys.exit(1)

    record = AdmissionRecord.load(record_path)

    if record.status == "rejected":
        print(f"ERROR: {qid} was rejected by scan — cannot approve", file=sys.stderr)
        sys.exit(1)
    if record.status not in ("scanned",):
        print(f"ERROR: {qid} status is '{record.status}' — must be 'scanned' to approve",
              file=sys.stderr)
        sys.exit(1)
    if not record.scan_verdict:
        print(f"ERROR: {qid} has not been scanned yet", file=sys.stderr)
        sys.exit(1)

    record.approval_time = _now_iso()
    record.reviewer = args.reviewer
    record.approval_notes = args.notes or ""
    record.status = "approved"
    record.save(record_path)

    _audit_append({
        "event": "approve",
        "quarantine_id": qid,
        "reviewer": args.reviewer,
        "scan_verdict": record.scan_verdict,
        "findings_at_approval": record.scan_findings_count,
    })

    print(f"Approved: {qid} by {args.reviewer}")
    print(f"  Scan verdict was: {record.scan_verdict}")
    print(f"  Notes: {record.approval_notes}")


def cmd_promote(args):
    qid = args.quarantine_id
    record_path = RECORDS_DIR / f"{qid}.json"
    if not record_path.exists():
        print(f"ERROR: No record for {qid}", file=sys.stderr)
        sys.exit(1)

    record = AdmissionRecord.load(record_path)

    if record.status != "approved":
        print(f"ERROR: {qid} status is '{record.status}' — must be 'approved' to promote",
              file=sys.stderr)
        sys.exit(1)

    dest = REGISTRY_DIR / record.skill_name / qid
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(QUARANTINE_DIR / qid / "skill", dest / "skill", dirs_exist_ok=True)

    manifest_path = dest / "MANIFEST.json"
    manifest_path.write_text(json.dumps({
        "quarantine_id": qid,
        "skill_name": record.skill_name,
        "publisher": record.publisher,
        "source": record.source,
        "promoted": _now_iso(),
        "reviewer": record.reviewer,
        "scan_verdict": record.scan_verdict,
        "sha256_manifest": record.sha256_manifest,
    }, indent=2))

    record.promote_time = _now_iso()
    record.status = "promoted"
    record.save(record_path)

    _audit_append({
        "event": "promote",
        "quarantine_id": qid,
        "destination": str(dest),
    })

    print(f"Promoted: {qid} → {dest}")
    print(f"  Manifest: {manifest_path}")


def cmd_audit_existing(args):
    skills_root = Path(args.skills_root).resolve()
    if not skills_root.is_dir():
        print(f"ERROR: {skills_root} is not a directory", file=sys.stderr)
        sys.exit(1)

    sys.path.insert(0, str(Path(__file__).parent))
    from scan_skill import scan_skill, format_text

    results = []
    for entry in sorted(skills_root.iterdir()):
        if entry.is_dir():
            has_skill_files = any(
                entry.rglob("*.py")) or any(
                entry.rglob("*.md")) or any(
                entry.rglob("*.sh"))
            if has_skill_files:
                r = scan_skill(entry, strict=args.strict)
                results.append(r)

    if args.json:
        out = []
        for r in results:
            out.append({
                "skill_name": r.skill_name,
                "verdict": r.verdict,
                "findings_count": len(r.findings),
                "critical": r.critical_count,
                "high": r.high_count,
                "file_count": r.file_count,
            })
        print(json.dumps(out, indent=2))
    else:
        print(f"=== Audit of {len(results)} skills in {skills_root} ===\n")
        for r in results:
            flag = "✗" if r.verdict == "FAIL" else ("⚠" if r.verdict == "REVIEW" else "✓")
            print(f"  {flag} {r.skill_name:40s} {r.verdict:8s} "
                  f"C={r.critical_count} H={r.high_count} "
                  f"total={len(r.findings)}")
        print()

        pass_count = sum(1 for r in results if r.verdict == "PASS")
        review_count = sum(1 for r in results if r.verdict == "REVIEW")
        fail_count = sum(1 for r in results if r.verdict == "FAIL")
        print(f"Summary: {pass_count} PASS, {review_count} REVIEW, {fail_count} FAIL")

    return results


def cmd_status(args):
    if args.quarantine_id:
        record_path = RECORDS_DIR / f"{args.quarantine_id}.json"
        if not record_path.exists():
            print(f"ERROR: No record for {args.quarantine_id}", file=sys.stderr)
            sys.exit(1)
        record = AdmissionRecord.load(record_path)
        print(json.dumps(asdict(record), indent=2))
    else:
        if not RECORDS_DIR.exists():
            print("No records found.")
            return
        records = []
        for f in sorted(RECORDS_DIR.glob("q-*.json")):
            if f.name.endswith(".scan.json"):
                continue
            r = AdmissionRecord.load(f)
            records.append(r)
        if not records:
            print("No records found.")
            return
        print(f"{'ID':50s} {'Status':12s} {'Verdict':8s} {'Skill':30s}")
        print("-" * 100)
        for r in records:
            print(f"{r.quarantine_id:50s} {r.status:12s} "
                  f"{r.scan_verdict or '-':8s} {r.skill_name:30s}")


def main():
    parser = argparse.ArgumentParser(description="Skill admission pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_q = sub.add_parser("quarantine", help="Quarantine a skill for review")
    p_q.add_argument("skill_path")
    p_q.add_argument("--source", default="local", choices=["clawhub", "local", "git"])
    p_q.add_argument("--publisher", default="")
    p_q.add_argument("--repo-url", default="")
    p_q.add_argument("--release", default="")

    p_s = sub.add_parser("scan", help="Scan a quarantined skill")
    p_s.add_argument("quarantine_id")
    p_s.add_argument("--strict", action="store_true")

    p_a = sub.add_parser("approve", help="Approve a scanned skill")
    p_a.add_argument("quarantine_id")
    p_a.add_argument("--reviewer", required=True)
    p_a.add_argument("--notes", default="")

    p_p = sub.add_parser("promote", help="Promote an approved skill to registry")
    p_p.add_argument("quarantine_id")

    p_ae = sub.add_parser("audit-existing", help="Audit all skills in a directory")
    p_ae.add_argument("skills_root")
    p_ae.add_argument("--json", action="store_true")
    p_ae.add_argument("--strict", action="store_true")

    p_st = sub.add_parser("status", help="Show pipeline status")
    p_st.add_argument("quarantine_id", nargs="?", default=None)

    args = parser.parse_args()

    if args.command == "quarantine":
        cmd_quarantine(args)
    elif args.command == "scan":
        cmd_scan(args)
    elif args.command == "approve":
        cmd_approve(args)
    elif args.command == "promote":
        cmd_promote(args)
    elif args.command == "audit-existing":
        cmd_audit_existing(args)
    elif args.command == "status":
        cmd_status(args)


if __name__ == "__main__":
    main()
