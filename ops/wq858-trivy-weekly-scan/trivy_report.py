#!/usr/bin/env python3
# wq#858 — parse Trivy JSON scan outputs (one file per `trivy image`/`trivy
# rootfs` invocation) into a single markdown report, and send an ntfy digest
# when any HIGH/CRITICAL finding is present. Pure stdlib so it runs unchanged
# on the bare Lumen host — no venv to keep alive for a once-a-week job.
import argparse
import json
import os
import subprocess
from datetime import datetime, timezone

DIGEST_SEVERITIES = {"HIGH", "CRITICAL"}
ALL_SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]

NTFY_BIN = os.environ.get("TRIVY_SCAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")
NTFY_TOPIC = os.environ.get("TRIVY_SCAN_NTFY_TOPIC", "lumen-alerts")


def load_findings(scan_dir):
    """Return [(artifact, target, vuln_dict), ...] across every *.json in scan_dir.

    Tolerates a trivy JSON doc whose Results/Vulnerabilities are missing or null
    (a clean target omits both rather than emitting an empty list).
    """
    findings = []
    for name in sorted(os.listdir(scan_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(scan_dir, name)) as f:
            try:
                doc = json.load(f)
            except json.JSONDecodeError:
                continue
        artifact = doc.get("ArtifactName", name)
        for result in doc.get("Results") or []:
            target = result.get("Target", artifact)
            for vuln in result.get("Vulnerabilities") or []:
                findings.append((artifact, target, vuln))
    return findings


def counts_by_severity(findings):
    counts = {s: 0 for s in ALL_SEVERITIES}
    for _, _, vuln in findings:
        sev = vuln.get("Severity", "UNKNOWN")
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def render_report(findings, scanned_at):
    counts = counts_by_severity(findings)
    lines = [
        f"# Trivy weekly scan — {scanned_at}",
        "",
        "| severity | count |",
        "|---|---|",
    ]
    lines += [f"| {sev} | {counts[sev]} |" for sev in ALL_SEVERITIES]
    lines.append("")

    digest = [f for f in findings if f[2].get("Severity") in DIGEST_SEVERITIES]
    if digest:
        lines.append("## HIGH+ findings")
        lines.append("")
        lines.append("| artifact | target | severity | id | package | installed | fixed |")
        lines.append("|---|---|---|---|---|---|---|")
        for artifact, target, vuln in digest:
            lines.append(
                f"| {artifact} | {target} | {vuln.get('Severity')} | "
                f"{vuln.get('VulnerabilityID')} | {vuln.get('PkgName')} | "
                f"{vuln.get('InstalledVersion')} | {vuln.get('FixedVersion', '-')} |"
            )
    else:
        lines.append("No HIGH/CRITICAL findings this run.")
    lines.append("")
    return "\n".join(lines)


def send_digest(findings, report_path, ntfy_bin=NTFY_BIN, topic=NTFY_TOPIC, run=subprocess.run):
    """Fire an ntfy digest iff there's at least one HIGH/CRITICAL finding. Returns
    the CompletedProcess, or None if nothing met the threshold (no-op, not a
    failure — most weeks should be silent)."""
    digest = [f for f in findings if f[2].get("Severity") in DIGEST_SEVERITIES]
    if not digest:
        return None
    counts = counts_by_severity(digest)
    artifacts = {artifact for artifact, _, _ in digest}
    msg = (
        f"Trivy weekly scan: {counts['CRITICAL']} CRITICAL, {counts['HIGH']} HIGH "
        f"across {len(artifacts)} artifact(s). Report: {report_path}"
    )
    return run(
        [ntfy_bin, "-t", topic, "-p", "high", "-T", "trivy-weekly-scan", msg],
        capture_output=True, text=True, timeout=20,
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan-dir", required=True, help="dir of trivy --format json outputs")
    ap.add_argument("--report-dir", required=True, help="dir to write the markdown report")
    ap.add_argument("--no-ntfy", action="store_true", help="skip the ntfy digest (testing)")
    args = ap.parse_args(argv)

    findings = load_findings(args.scan_dir)
    scanned_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = render_report(findings, scanned_at)

    os.makedirs(args.report_dir, exist_ok=True)
    date_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = os.path.join(args.report_dir, f"trivy_scan_{date_stamp}.md")
    with open(report_path, "w") as f:
        f.write(report)

    if not args.no_ntfy:
        send_digest(findings, report_path)

    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
