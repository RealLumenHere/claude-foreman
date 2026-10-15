"""`limpet-workspace` CLI -- export a Limpet investigation workspace to a report.

The operator/agent front door to the reporting core (``workspace_report.py``).
Mirrors the ``limpet`` CLI conventions (``limpet/cli.py``): a global ``--json`` for
machine-readable status, subcommands via ``set_defaults(func=...)``, and
``--*-dir`` overrides of the ``/etc/limpet`` defaults so a test can point the
template search at a tmp dir (a falsifiable boundary, outside the real /etc).

  limpet-workspace export <workspace> --format md|html|json   write a report
  limpet-workspace templates list                              resolvable templates
  limpet-workspace templates validate <path>                   check a custom template
  limpet-workspace obligations list|open|resolve|waive <ws>    the RE obligation ledger
  limpet-workspace obligations verify <ws>                     exit 3 while any is open

The agent chat command ``/export`` (agent-chat GUI, design/wq3903) maps straight
onto ``export`` via ``chat_export`` below, so the agent and a headless SSH operator
drive the exact same code path.

Exit 0 on success, 1 on usage/not-found, 2 on a report/template/ledger error, matching
``limpet/cli.py`` so scripts can branch on the code; ``obligations verify`` exits 3
when done is refused because an obligation is still open.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import obligations as ob_ledger
import workspace_report as wr


def _emit(obj, as_json):
    if as_json:
        print(json.dumps(obj, indent=2, sort_keys=True))


def _parse_source_date(val):
    if val is None or val == "":
        return None
    if val.lower() == "none":
        return 0.0
    try:
        return float(val)
    except ValueError:
        import datetime as dt
        return dt.datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp()


def _cmd_export(args):
    formats = [f.strip() for f in args.format.split(",")] if "," in args.format else [args.format]
    try:
        status = wr.export(
            args.workspace,
            formats=tuple(formats),
            template=args.template,
            out_dir=args.out,
            templates_dir=args.templates_dir,
            manifest_name=args.manifest,
            at=args.at,
            log_cap=(0 if args.full_log else wr.DEFAULT_LOG_CAP),
            source_date=_parse_source_date(args.source_date),
            redact=not args.no_redact,
        )
    except wr.ReportError as exc:
        _emit({"ok": False, "error": str(exc)}, args.json)
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.no_redact:
        print("warning: --no-redact: report may contain unmasked secrets", file=sys.stderr)
    _emit({"ok": True, **status}, args.json)
    if not args.json:
        for fmt, path in sorted(status["written"].items()):
            print(f"{fmt}\t{path}")
    return 0


def _cmd_templates_list(args):
    roots = []
    if args.workspace:
        roots.append(os.path.join(os.path.abspath(args.workspace), "report-templates"))
    roots.append(args.templates_dir or wr.SITE_TEMPLATES_DIR)
    roots.append(wr.BUILTIN_TEMPLATES_DIR)
    seen, found = set(), []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            cand = os.path.join(root, name)
            if name in seen or not os.path.isdir(cand):
                continue
            if not os.path.exists(os.path.join(cand, "template.toml")):
                continue
            seen.add(name)
            found.append({"name": name, "source": root,
                          "formats": _declared_formats(cand)})
    _emit({"ok": True, "templates": found}, args.json)
    if not args.json:
        for t in found:
            print(f"{t['name']}\t{','.join(t['formats'])}\t{t['source']}")
    return 0


def _declared_formats(template_dir):
    fmts = []
    for fmt in wr.ALL_FORMATS:
        if os.path.exists(os.path.join(template_dir, f"report.{fmt}.j2")):
            fmts.append(fmt)
    return fmts


def _cmd_templates_validate(args):
    path = args.path
    if not os.path.isdir(path):
        _emit({"ok": False, "error": f"not a directory: {path}"}, args.json)
        print(f"error: not a template directory: {path}", file=sys.stderr)
        return 1
    formats = _declared_formats(path)
    if not formats:
        _emit({"ok": False, "error": "declares no report.<fmt>.j2"}, args.json)
        print("error: template declares no report.md.j2/report.html.j2", file=sys.stderr)
        return 2
    # Dry-render each declared format against a fixture IR so a broken template is
    # caught before an engagement relies on it (AC probe: input from outside the repo).
    fixture = _fixture_ir()
    errors = []
    for fmt in formats:
        try:
            wr.render_template(fixture, fmt, os.path.abspath(path))
        except Exception as exc:  # jinja TemplateError, ReportError, etc.
            errors.append({"format": fmt, "error": f"{type(exc).__name__}: {exc}"})
    ok = not errors
    _emit({"ok": ok, "formats": formats, "errors": errors}, args.json)
    if not args.json:
        if ok:
            print(f"OK {path} renders {','.join(formats)}")
        else:
            for e in errors:
                print(f"FAIL {e['format']}: {e['error']}", file=sys.stderr)
    return 0 if ok else 2


def _ob_fail(exc, as_json):
    _emit({"ok": False, "error": str(exc)}, as_json)
    print(f"error: {exc}", file=sys.stderr)
    return 2


def _ob_row(ob):
    return f"{ob['id']}\t{ob['status']}\t{ob['severity']}\t{ob['opened_by']}\t{ob['claim']}"


def _cmd_ob_list(args):
    try:
        obs = ob_ledger.list_obligations(args.workspace, status=args.status)
    except ob_ledger.ObligationError as exc:
        return _ob_fail(exc, args.json)
    _emit({"ok": True, "obligations": obs}, args.json)
    if not args.json:
        for ob in obs:
            print(_ob_row(ob))
    return 0


def _cmd_ob_open(args):
    try:
        ob = ob_ledger.open_obligation(args.workspace, args.claim, opened_by=args.opened_by,
                                       evidence=args.evidence, severity=args.severity,
                                       by=args.by, commit=not args.no_commit)
    except ob_ledger.ObligationError as exc:
        return _ob_fail(exc, args.json)
    _emit({"ok": True, "obligation": ob}, args.json)
    if not args.json:
        print(_ob_row(ob))
    return 0


def _cmd_ob_close(args):
    fn = ob_ledger.resolve if args.obcmd == "resolve" else ob_ledger.waive
    try:
        ob = fn(args.workspace, args.id, evidence=args.evidence, rationale=args.rationale,
                by=args.by, expected_revision=args.expected_revision,
                commit=not args.no_commit)
    except ob_ledger.ObligationError as exc:
        return _ob_fail(exc, args.json)
    _emit({"ok": True, "obligation": ob}, args.json)
    if not args.json:
        print(_ob_row(ob))
    return 0


def _cmd_ob_verify(args):
    try:
        result = ob_ledger.check_done(args.workspace)
    except ob_ledger.ObligationError as exc:
        return _ob_fail(exc, args.json)
    _emit(result, args.json)
    if not args.json:
        if result["ok"]:
            print("OK no open obligations")
        else:
            for ob in result["open"]:
                print(_ob_row(ob), file=sys.stderr)
            print(f"REFUSED done: {len(result['open'])} open obligation(s)", file=sys.stderr)
    return 0 if result["ok"] else 3


def _fixture_ir():
    """A minimal but complete IR used to dry-render a template under validation."""
    return {
        "report_meta": {"schema_version": "1", "report_id": "rpt-fixture0000",
                        "template": "fixture", "generator_version": wr.GENERATOR_VERSION,
                        "workspace_commit": "0" * 40, "generated_at": "1970-01-01T00:00:00Z",
                        "source_date_epoch": 0, "redacted": True},
        "target": {"target_id": "fixture-device", "class": "firmware",
                   "identity": {"vendor": "Acme", "model": "FX-1", "version": "1.0"},
                   "checksums": [{"path": "target/fw.bin", "sha256": "ab" * 32, "size": 1024}],
                   "hardware_bound": [{"capability": "debug_probe", "adapter": "ST-Link/V2", "vid_pid": "0483:3748"}]},
        "timeline": [{"ts": "1970-01-01T00:00:00Z", "kind": "finding", "ref": "F-001", "summary": "x", "actor": "agent"}],
        "tools_used": [{"plugin": "binwalk", "version": "2.3.4", "trust": "core", "invocations": 1,
                        "first_ts": "1970-01-01T00:00:00Z", "last_ts": "1970-01-01T00:00:00Z"}],
        "firmware_checksums": [{"path": "target/fw.bin", "sha256": "ab" * 32, "size": 1024}],
        "git_summary": {"commit_count": 1, "first_commit": "0" * 40, "head": "0" * 40,
                        "shortlog": [{"sha": "0" * 40, "ts": "1970-01-01T00:00:00Z", "subject": "init"}]},
        "findings": [{"id": "F-001", "title": "Fixture finding", "severity": "high",
                      "summary": "A fixture finding for template validation.",
                      "evidence": [{"path": "evidence/x", "sha256": "cd" * 32, "locator": "line 1"}],
                      "remediation": "Patch it."}],
        "obligations": [{"id": "OB-001", "claim": "Fixture question?", "opened_by": "static",
                         "opened_at": "1970-01-01T00:00:00Z", "severity": "medium",
                         "evidence": ["evidence/x"], "status": "resolved", "revision": 2,
                         "resolution": {"disposition": "verified", "rationale": "answered",
                                        "evidence": ["evidence/x#L1"], "ts": "1970-01-01T00:00:00Z",
                                        "by": "agent"},
                         "history": []}],
        "obligations_summary": {"total": 1, "open": 0, "resolved": 1, "waived": 0, "done_ready": True},
    }


def chat_export(line, workspace, templates_dir=None, source_date=None):
    """Handle the agent-chat ``/export`` command.

    ``/export`` -> markdown (default); ``/export html`` / ``/export json`` /
    ``/export all``; ``/export md full-technical`` picks a template. Returns the
    export status dict. The agent surfaces this; it drives the same code as the CLI.
    """
    parts = line.split()
    if parts and parts[0].lstrip("/").lower() == "export":
        parts = parts[1:]
    fmt = parts[0] if parts else "md"
    template = parts[1] if len(parts) > 1 else wr.DEFAULT_TEMPLATE
    formats = wr.ALL_FORMATS if fmt == "all" else (fmt,)
    return wr.export(workspace, formats=formats, template=template,
                     templates_dir=templates_dir, source_date=source_date)


def build_parser():
    p = argparse.ArgumentParser(prog="limpet-workspace",
                                description="Export a Limpet investigation workspace to a report.")
    p.add_argument("--json", action="store_true", help="machine-readable status on stdout")
    sub = p.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("export", help="render a workspace to md/html/json")
    ex.add_argument("workspace", help="workspace dir (path or <target-id> under /var/limpet/workspaces)")
    ex.add_argument("--format", default="md", help="md (default), html, json, all, or a comma list")
    ex.add_argument("--template", default=wr.DEFAULT_TEMPLATE, help="template NAME or PATH")
    ex.add_argument("--out", default=None, help="output dir (default <workspace>/reports/<report-id>/)")
    ex.add_argument("--templates-dir", default=None, help=f"site template dir (default {wr.SITE_TEMPLATES_DIR})")
    ex.add_argument("--manifest", default="manifest.json", help="manifest filename in the workspace")
    ex.add_argument("--at", default=None, help="pin to a workspace git commit (default HEAD)")
    ex.add_argument("--full-log", action="store_true", help="lift the git shortlog cap")
    ex.add_argument("--source-date", default=None, help="EPOCH|iso|none for reproducible generated_at")
    ex.add_argument("--no-redact", action="store_true", help="skip credential masking (warns)")
    ex.set_defaults(func=_cmd_export)

    tp = sub.add_parser("templates", help="list or validate report templates")
    tsub = tp.add_subparsers(dest="tcmd", required=True)
    tl = tsub.add_parser("list", help="enumerate resolvable templates")
    tl.add_argument("--templates-dir", default=None)
    tl.add_argument("--workspace", default=None, help="also search this workspace's report-templates/")
    tl.set_defaults(func=_cmd_templates_list)
    tv = tsub.add_parser("validate", help="dry-render a custom template against a fixture IR")
    tv.add_argument("path", help="template directory to validate")
    tv.set_defaults(func=_cmd_templates_validate)

    op = sub.add_parser("obligations", help="the workspace's open RE obligations (obligations.json)")
    osub = op.add_subparsers(dest="obcmd", required=True)
    ol = osub.add_parser("list", help="list obligations")
    ol.add_argument("workspace")
    ol.add_argument("--status", choices=ob_ledger.STATUSES, default=None)
    ol.set_defaults(func=_cmd_ob_list)
    oo = osub.add_parser("open", help="record a new open obligation")
    oo.add_argument("workspace")
    oo.add_argument("claim", help="the claim or question that must be answered")
    oo.add_argument("--opened-by", required=True, help="analysis step that raised it")
    oo.add_argument("--severity", choices=ob_ledger.SEVERITIES, default="medium")
    oo.add_argument("--evidence", action="append", default=[], help="evidence ref (repeatable)")
    oo.add_argument("--by", default="operator")
    oo.add_argument("--no-commit", action="store_true", help="write the ledger without a git commit")
    oo.set_defaults(func=_cmd_ob_open)
    for verb, needs_ev in (("resolve", True), ("waive", False)):
        oc = osub.add_parser(verb, help=("close with evidence" if needs_ev else "close without answering, with a reason"))
        oc.add_argument("workspace")
        oc.add_argument("id", help="obligation id, e.g. OB-001")
        oc.add_argument("--rationale", required=True)
        oc.add_argument("--evidence", action="append", default=[],
                        help="workspace-relative path[#locator] or REA ev_ id (repeatable)"
                        + ("; at least one required" if needs_ev else ""))
        oc.add_argument("--expected-revision", type=int, default=None)
        oc.add_argument("--by", default="operator")
        oc.add_argument("--no-commit", action="store_true")
        oc.set_defaults(func=_cmd_ob_close)
    ov = osub.add_parser("verify", help="the agent verify-step gate: exit 3 while any obligation is open")
    ov.add_argument("workspace")
    ov.set_defaults(func=_cmd_ob_verify)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
