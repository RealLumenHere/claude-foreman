# wq#858 — tests for trivy_report.py. No trivy binary and no root available in
# this sandbox (guard.sh denies docker beyond ps/logs and every root-elevation
# verb — see README.md), so these exercise the report/digest logic in isolation
# against fixture JSON shaped like real `trivy --format json` output.
import json
import os

import trivy_report as tr

CLEAN_IMAGE = {
    "ArtifactName": "ghcr.io/example/clean:1.0",
    "Results": [
        {"Target": "ghcr.io/example/clean:1.0 (alpine 3.20)", "Class": "os-pkgs"},
    ],
}

VULN_IMAGE = {
    "ArtifactName": "mongo:7",
    "Results": [
        {
            "Target": "mongo:7 (debian 12.5)",
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "CVE-2026-1111",
                    "PkgName": "openssl",
                    "InstalledVersion": "3.0.13-1",
                    "FixedVersion": "3.0.14-1",
                    "Severity": "CRITICAL",
                },
                {
                    "VulnerabilityID": "CVE-2026-2222",
                    "PkgName": "libc6",
                    "InstalledVersion": "2.36-9",
                    "FixedVersion": "2.36-10",
                    "Severity": "HIGH",
                },
                {
                    "VulnerabilityID": "CVE-2026-3333",
                    "PkgName": "curl",
                    "InstalledVersion": "7.88.1",
                    "Severity": "MEDIUM",
                },
            ],
        }
    ],
}

ROOTFS = {
    "ArtifactName": "/",
    "Results": [
        {
            "Target": "Node.js",
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "CVE-2026-4444",
                    "PkgName": "lodash",
                    "InstalledVersion": "4.17.20",
                    "Severity": "HIGH",
                },
            ],
        }
    ],
}


def write_fixtures(scan_dir, *docs):
    for i, doc in enumerate(docs):
        with open(os.path.join(scan_dir, f"scan_{i}.json"), "w") as f:
            json.dump(doc, f)


def test_load_findings_flattens_across_files_and_skips_clean_targets(tmp_path):
    write_fixtures(tmp_path, CLEAN_IMAGE, VULN_IMAGE, ROOTFS)
    findings = tr.load_findings(str(tmp_path))
    ids = sorted(v["VulnerabilityID"] for _, _, v in findings)
    assert ids == ["CVE-2026-1111", "CVE-2026-2222", "CVE-2026-3333", "CVE-2026-4444"]


def test_load_findings_ignores_non_json_files(tmp_path):
    write_fixtures(tmp_path, VULN_IMAGE)
    (tmp_path / "notes.txt").write_text("not json")
    findings = tr.load_findings(str(tmp_path))
    assert len(findings) == 3


def test_load_findings_tolerates_malformed_json(tmp_path):
    write_fixtures(tmp_path, VULN_IMAGE)
    (tmp_path / "scan_broken.json").write_text("{not valid json")
    findings = tr.load_findings(str(tmp_path))
    assert len(findings) == 3


def test_counts_by_severity(tmp_path):
    write_fixtures(tmp_path, VULN_IMAGE, ROOTFS)
    findings = tr.load_findings(str(tmp_path))
    counts = tr.counts_by_severity(findings)
    assert counts == {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 1, "LOW": 0, "UNKNOWN": 0}


def test_render_report_lists_high_plus_only_in_findings_table(tmp_path):
    write_fixtures(tmp_path, VULN_IMAGE)
    findings = tr.load_findings(str(tmp_path))
    report = tr.render_report(findings, "2026-09-02T00:00:00Z")
    assert "CVE-2026-1111" in report
    assert "CVE-2026-2222" in report
    assert "CVE-2026-3333" not in report  # MEDIUM stays out of the findings table
    assert "| CRITICAL | 1 |" in report
    assert "| MEDIUM | 1 |" in report


def test_render_report_clean_run_says_so(tmp_path):
    write_fixtures(tmp_path, CLEAN_IMAGE)
    findings = tr.load_findings(str(tmp_path))
    report = tr.render_report(findings, "2026-09-02T00:00:00Z")
    assert "No HIGH/CRITICAL findings this run." in report


def test_send_digest_fires_ntfy_on_high_plus(tmp_path):
    write_fixtures(tmp_path, VULN_IMAGE)
    findings = tr.load_findings(str(tmp_path))

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)

        class Result:
            returncode = 0

        return Result()

    result = tr.send_digest(findings, "/home/lumen/maint/trivy/trivy_scan_2026-09-02.md",
                             ntfy_bin="/fake/ntfy-send", topic="lumen-alerts", run=fake_run)
    assert result is not None
    assert len(calls) == 1
    argv = calls[0]
    assert argv[0] == "/fake/ntfy-send"
    assert "-t" in argv and "lumen-alerts" in argv
    assert "-p" in argv and "high" in argv
    msg = argv[-1]
    assert "1 CRITICAL" in msg
    assert "1 HIGH" in msg
    assert "trivy_scan_2026-09-02.md" in msg


def test_send_digest_is_noop_when_nothing_high_plus(tmp_path):
    write_fixtures(tmp_path, CLEAN_IMAGE)
    findings = tr.load_findings(str(tmp_path))

    calls = []
    result = tr.send_digest(findings, "irrelevant", run=lambda argv, **kw: calls.append(argv))
    assert result is None
    assert calls == []


def test_main_writes_dated_report_and_skips_ntfy_flag(tmp_path, capsys, monkeypatch):
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    write_fixtures(scan_dir, VULN_IMAGE)
    report_dir = tmp_path / "reports"

    monkeypatch.setattr(tr, "datetime", tr.datetime)  # keep real clock; just assert shape below
    rc = tr.main(["--scan-dir", str(scan_dir), "--report-dir", str(report_dir), "--no-ntfy"])
    assert rc == 0

    written = list(report_dir.glob("trivy_scan_*.md"))
    assert len(written) == 1
    assert "CVE-2026-1111" in written[0].read_text()

    printed_path = capsys.readouterr().out.strip()
    assert printed_path == str(written[0])
