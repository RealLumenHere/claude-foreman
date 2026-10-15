"""wq#3934 -- tests for the limpet-workspace reporting/export system.

Builds a throwaway workspace in tmp (manifest + findings + a real git repo),
exports it to md/html/json, and checks: content coverage, HTML escaping of
untrusted target strings, credential masking, byte-for-byte reproducibility,
template resolution/validation, and the /export chat dispatch. The
--templates-dir boundary is probed with a template OUTSIDE the built-in set
(a tmp dir), so a search-order bug can actually fail the test (wq#2630).
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workspace_cli as wc  # noqa: E402
import workspace_report as wr  # noqa: E402


def _git(ws, *args):
    subprocess.run(["git", "-C", ws, *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
                        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"})


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / "evidence" / "findings").mkdir(parents=True)
    (ws / "notes").mkdir()
    manifest = {
        "target_id": "acme-router-v2",
        "class": "firmware",
        "identity": {"vendor": "Acme", "model": "<Router> & Co", "version": "2.1"},
        "checksums": [{"path": "target/fw.bin", "sha256": "ab" * 32, "size": 8388608}],
        "hardware_bound": [{"capability": "debug_probe", "adapter": "ST-Link/V2", "vid_pid": "0483:3748"}],
        "tool_invocations": [
            {"seq": 1, "ts": "2026-01-01T01:00:00Z", "plugin": "unblob", "version": "1.0",
             "trust": "core", "summary": "extracted rootfs"},
            {"seq": 2, "ts": "2026-01-01T02:00:00Z", "plugin": "unblob", "version": "1.0",
             "trust": "core", "summary": "second pass"},
            {"seq": 3, "ts": "2026-01-01T03:00:00Z", "plugin": "binwalk", "version": "2.3.4",
             "trust": "community", "summary": "signature scan"},
        ],
    }
    (ws / "manifest.json").write_text(json.dumps(manifest))
    # A finding whose summary carries a target-extracted secret and HTML metacharacters.
    secret = "gh" "p_" + "A" * 36
    (ws / "evidence" / "findings" / "F-002.json").write_text(json.dumps({
        "id": "F-002", "title": "Creds in <config>", "severity": "critical",
        "summary": f"Baked-in token {secret} found in /etc & /tmp",
        "evidence": [{"path": "evidence/rootfs/etc/token", "sha256": "cd" * 32, "locator": "line 1"}],
        "remediation": "Rotate it.",
    }))
    (ws / "evidence" / "findings" / "F-010.json").write_text(json.dumps({
        "id": "F-010", "title": "Minor", "severity": "low", "summary": "nit"}))
    _git(ws, "init", "-q")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "snapshot investigation")
    return str(ws), secret


def test_collect_ir_shape(workspace):
    ws, _ = workspace
    ir = wr.collect(ws, source_date=0.0)
    assert ir["target"]["target_id"] == "acme-router-v2"
    assert ir["target"]["hardware_bound"][0]["vid_pid"] == "0483:3748"
    # unblob folded to one row with 2 invocations; binwalk separate.
    tools = {t["plugin"]: t for t in ir["tools_used"]}
    assert tools["unblob"]["invocations"] == 2 and tools["binwalk"]["invocations"] == 1
    # findings severity-ranked: critical before low.
    assert [f["id"] for f in ir["findings"]] == ["F-002", "F-010"]
    assert ir["git_summary"]["commit_count"] == 1 and ir["git_summary"]["head"]
    # timeline merges commit + tool + finding events, time-ordered.
    kinds = {e["kind"] for e in ir["timeline"]}
    assert {"commit", "tool_invocation", "finding"} <= kinds


def test_masking_applied_to_findings(workspace):
    ws, secret = workspace
    ir = wr.collect(ws, source_date=0.0)
    blob = json.dumps(ir)
    assert secret not in blob
    assert "MASKED:" in blob


def test_export_three_formats(tmp_path, workspace):
    ws, secret = workspace
    out = tmp_path / "out"
    status = wr.export(ws, formats=("md", "html", "json"), out_dir=str(out), source_date=0.0)
    for fmt in ("md", "html", "json"):
        assert os.path.exists(status["written"][fmt])
    md = open(status["written"]["md"]).read()
    html = open(status["written"]["html"]).read()
    data = json.load(open(status["written"]["json"]))
    # content coverage
    assert "acme-router-v2" in md and "0483:3748" in md and "unblob" in md
    assert "Workspace git summary" in md and "Firmware checksums" in md
    # HTML escapes untrusted target strings (the finding title has < >).
    assert "&lt;config&gt;" in html and "<config>" not in html
    # secret masked in every format
    assert secret not in md and secret not in html and secret not in json.dumps(data)
    # JSON is the IR verbatim
    assert data["target"]["target_id"] == "acme-router-v2"


def test_default_format_is_markdown(tmp_path, workspace):
    ws, _ = workspace
    status = wr.export(ws, out_dir=str(tmp_path / "o"), source_date=0.0)
    assert set(status["written"]) == {"md"}


def test_reports_dir_default_location(workspace):
    ws, _ = workspace
    status = wr.export(ws, formats=("json",), source_date=0.0)
    assert status["out_dir"].startswith(os.path.join(ws, "reports"))
    assert os.path.exists(os.path.join(ws, "reports", status["report_id"], "report.json"))


def test_reproducible_bytes(tmp_path, workspace):
    ws, _ = workspace
    a = wr.export(ws, formats=("md", "html", "json"), out_dir=str(tmp_path / "a"), source_date=0.0)
    b = wr.export(ws, formats=("md", "html", "json"), out_dir=str(tmp_path / "b"), source_date=0.0)
    for fmt in ("md", "html", "json"):
        assert open(a["written"][fmt], "rb").read() == open(b["written"][fmt], "rb").read()
    assert a["report_id"] == b["report_id"]


def test_no_manifest_errors(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(wr.ReportError):
        wr.collect(str(empty))


def test_template_search_order_boundary(tmp_path, workspace):
    """A site template dir OUTSIDE the built-ins is honored (falsifiable boundary)."""
    ws, _ = workspace
    site = tmp_path / "site-templates"
    (site / "tiny").mkdir(parents=True)
    (site / "tiny" / "template.toml").write_text('[template]\nname="tiny"\nversion="1"\nformats=["md"]\n')
    (site / "tiny" / "report.md.j2").write_text("TINY {{ target.target_id }}\n")
    out = wr.export(ws, formats=("md",), template="tiny", templates_dir=str(site),
                    out_dir=str(tmp_path / "o"), source_date=0.0)
    assert open(out["written"]["md"]).read() == "TINY acme-router-v2\n"
    # a template name that exists nowhere must fail, not silently fall through
    with pytest.raises(wr.ReportError):
        wr.export(ws, formats=("md",), template="does-not-exist", templates_dir=str(site),
                  out_dir=str(tmp_path / "o2"), source_date=0.0)


def test_builtin_exec_summary_template(tmp_path, workspace):
    ws, _ = workspace
    out = wr.export(ws, formats=("md",), template="exec-summary",
                    out_dir=str(tmp_path / "o"), source_date=0.0)
    md = open(out["written"]["md"]).read()
    assert "Executive summary" in md and "1 critical" in md


def test_cli_export_json_status(tmp_path, workspace, capsys):
    ws, _ = workspace
    rc = wc.main(["--json", "export", ws, "--format", "json",
                  "--out", str(tmp_path / "o"), "--source-date", "none"])
    assert rc == 0
    status = json.loads(capsys.readouterr().out)
    assert status["ok"] and "json" in status["written"]


def test_cli_templates_list_includes_builtins(capsys):
    rc = wc.main(["--json", "templates", "list"])
    assert rc == 0
    names = {t["name"] for t in json.loads(capsys.readouterr().out)["templates"]}
    assert {"full-technical", "exec-summary"} <= names


def test_cli_templates_validate_good_and_bad(tmp_path, capsys):
    good = tmp_path / "good"
    good.mkdir()
    (good / "report.md.j2").write_text("{{ target.target_id }}\n")
    assert wc.main(["--json", "templates", "validate", str(good)]) == 0
    capsys.readouterr()
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "report.md.j2").write_text("{{ target.missing_attr.explode() }}\n")
    rc = wc.main(["--json", "templates", "validate", str(bad)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and not out["ok"] and out["errors"]


def test_chat_export_dispatch(tmp_path, workspace):
    ws, _ = workspace
    status = wc.chat_export("/export html", ws, source_date=0.0)
    assert "html" in status["written"] and os.path.exists(status["written"]["html"])
    # bare /export defaults to markdown
    status2 = wc.chat_export("/export", ws, source_date=0.0)
    assert set(status2["written"]) == {"md"}


def test_no_redact_flag_marks_report(tmp_path, workspace):
    ws, secret = workspace
    out = wr.export(ws, formats=("json",), out_dir=str(tmp_path / "o"),
                    source_date=0.0, redact=False)
    data = json.load(open(out["written"]["json"]))
    assert data["report_meta"]["redacted"] is False
    # unredacted: the secret is present verbatim
    assert secret in json.dumps(data)
