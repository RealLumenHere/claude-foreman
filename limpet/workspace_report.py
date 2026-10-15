"""wq#3934 -- LIMPET workspace reporting/export core.

Turns a Limpet investigation *workspace* into an operator-readable report in
three formats: Markdown (default), styled printable HTML, and machine-readable
JSON. The operator -- not a paying client -- is the reader; the report is the
agent's findings made legible and re-derivable, not a sales deliverable.

Design (full rationale: ``docs/reporting-spec.md``):

  collect -> assemble IR -> mask -> render

All three formats render from one canonical Investigation Record (IR) assembled
once from committed workspace state, so the formats cannot disagree. JSON *is*
the IR; Markdown and HTML are Jinja2 views of it. A report is *derived*, never
authored: every field is read from the workspace

  <workspace>/
    manifest.json   target identity, checksums, hardware bound, tool-invocation log
    evidence/findings/*.json   findings the agent/operator committed during analysis
    obligations.json           open/resolved/waived RE obligations (``obligations.py``)
    workspace.git (or .git)    the analysis history -> git log summary
    notes/*.md      working notes (optional timeline events)

and reports are written to ``<workspace>/reports/<report-id>/`` (the ticket's
``workspace/reports/`` location).

The module shells out to ``git`` read-only for the log summary and never executes
anything from inside the untrusted ``target/`` tree (fleet instinct: a planted
``json.py`` must not run on import). Strings quoted from the target or tool output
are credential-masked once on the IR (fleet instinct 4) before any renderer sees
them, so every format redacts identically.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import subprocess

import obligations

SCHEMA_VERSION = "1"
GENERATOR_VERSION = "limpet-report/1.0.0"
DEFAULT_TEMPLATE = "full-technical"
DEFAULT_LOG_CAP = 200
MASK_LABEL = "MASKED"

# Package-local built-in templates; site templates live in /etc/limpet/report-templates
# (the ticket's path), workspace-local ones in <workspace>/report-templates.
BUILTIN_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report-templates")
SITE_TEMPLATES_DIR = "/etc/limpet/report-templates"


class ReportError(Exception):
    """A workspace that cannot be turned into a report (missing manifest, bad template)."""


# --- credential masking (vendored; mirrors harness/masking.py posture so the packaged
# limpet tool stays self-contained, fleet instinct 4) --------------------------------

# Each pattern matches a credential-shaped run; the whole match is replaced by a
# «MASKED:sha8» token so a reader can still tell "same secret seen twice" apart without
# the secret itself ever reaching an HTML/MD/JSON file that gets handed around.
_CRED_PATTERNS = [
    re.compile(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{16,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bcf[a-z]*_[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[A-Z0-9]{12,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
    # NAME=<secret> / NAME: <secret> where NAME ends in a secret-ish word.
    re.compile(r"(?i)\b([A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD|PASSWD|PAT))\s*[=:]\s*([^\s\"']{8,})"),
]


def _mask_token(secret: str) -> str:
    return "«MASKED:" + hashlib.sha256(secret.encode("utf-8", "replace")).hexdigest()[:8] + "»"


def mask_text(text: str) -> str:
    """Replace credential-shaped substrings in ``text`` with stable masked tokens."""
    if not text:
        return text
    out = text
    for pat in _CRED_PATTERNS:
        if pat.groups >= 2:
            def _kv(m):
                return m.group(0)[: m.start(2) - m.start(0)] + _mask_token(m.group(2))
            out = pat.sub(_kv, out)
        else:
            out = pat.sub(lambda m: _mask_token(m.group(0)), out)
    return out


def _mask_tree(obj):
    """Walk an IR fragment, masking every string in place (lists/dicts recursed)."""
    if isinstance(obj, str):
        return mask_text(obj)
    if isinstance(obj, list):
        return [_mask_tree(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _mask_tree(v) for k, v in obj.items()}
    return obj


# --- small helpers -------------------------------------------------------------------

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _rfc3339(epoch: float | None) -> str:
    dt = _dt.datetime.fromtimestamp(epoch if epoch is not None else _now_epoch(), tz=_dt.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_epoch() -> float:
    return _dt.datetime.now(tz=_dt.timezone.utc).timestamp()


def _git_dir(workspace: str) -> str | None:
    for cand in (os.path.join(workspace, "workspace.git"), os.path.join(workspace, ".git")):
        if os.path.exists(cand):
            return cand
    return None


def _git(workspace: str, gitdir: str, *args: str) -> str:
    """Read-only git call pinned to the workspace repo; '' on any failure."""
    if os.path.isdir(gitdir) and os.path.basename(gitdir) == ".git":
        base = ["git", "-C", workspace]
    else:
        base = ["git", "--git-dir", gitdir]
    try:
        res = subprocess.run(base + list(args), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout if res.returncode == 0 else ""


# --- collectors ----------------------------------------------------------------------

def _collect_manifest(workspace: str, manifest_name: str) -> dict:
    path = os.path.join(workspace, manifest_name)
    if not os.path.exists(path):
        raise ReportError(f"no {manifest_name} in {workspace}: not a Limpet workspace")
    try:
        with open(path, encoding="utf-8") as fh:
            man = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ReportError(f"cannot read {manifest_name}: {exc}") from exc
    if not isinstance(man, dict):
        raise ReportError(f"{manifest_name} is not a JSON object")
    return man


def _collect_findings(workspace: str) -> list[dict]:
    fdir = os.path.join(workspace, "evidence", "findings")
    if not os.path.isdir(fdir):
        return []
    findings = []
    for name in sorted(os.listdir(fdir)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(fdir, name), encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict):
            rec.setdefault("id", os.path.splitext(name)[0])
            findings.append(rec)
    _SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings.sort(key=lambda f: (_SEV.get(str(f.get("severity", "info")).lower(), 5), str(f.get("id", ""))))
    return findings


def _collect_git(workspace: str, at: str | None, log_cap: int) -> tuple[dict, str | None, list[dict]]:
    gitdir = _git_dir(workspace)
    if gitdir is None:
        return ({"commit_count": 0, "first_commit": None, "head": None, "shortlog": []}, None, [])
    rev = at or "HEAD"
    head = _git(workspace, gitdir, "rev-parse", rev).strip() or None
    count_s = _git(workspace, gitdir, "rev-list", "--count", rev).strip()
    count = int(count_s) if count_s.isdigit() else 0
    first = _git(workspace, gitdir, "rev-list", "--max-parents=0", rev).strip().splitlines()
    first_commit = first[-1] if first else None
    cap = 10 ** 9 if log_cap <= 0 else log_cap
    raw = _git(workspace, gitdir, "log", f"-n{cap}", "--no-color",
               "--pretty=format:%H%x1f%cI%x1f%s", rev)
    shortlog, events = [], []
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        sha, ts, subject = parts
        shortlog.append({"sha": sha, "ts": ts, "subject": subject})
        events.append({"ts": ts, "kind": "commit", "ref": sha[:12], "summary": subject, "actor": "agent"})
    summary = {"commit_count": count, "first_commit": first_commit, "head": head, "shortlog": shortlog}
    return summary, head, events


def _tool_timeline(tools: list[dict]) -> list[dict]:
    events = []
    for inv in tools:
        if not isinstance(inv, dict):
            continue
        events.append({
            "ts": inv.get("ts", ""),
            "kind": "tool_invocation",
            "ref": str(inv.get("seq", "")),
            "summary": inv.get("summary") or f"{inv.get('plugin', '?')} invoked",
            "actor": inv.get("actor", "agent"),
        })
    return events


def _fold_tools(invocations: list[dict]) -> list[dict]:
    """Collapse the raw tool-invocation log into one row per (plugin, version)."""
    folded: dict[tuple, dict] = {}
    for inv in invocations:
        if not isinstance(inv, dict):
            continue
        key = (inv.get("plugin", "?"), inv.get("version", ""))
        row = folded.get(key)
        ts = inv.get("ts", "")
        if row is None:
            folded[key] = {"plugin": key[0], "version": key[1], "trust": inv.get("trust", "unknown"),
                           "invocations": 1, "first_ts": ts, "last_ts": ts}
        else:
            row["invocations"] += 1
            if ts and (not row["first_ts"] or ts < row["first_ts"]):
                row["first_ts"] = ts
            if ts and ts > row["last_ts"]:
                row["last_ts"] = ts
    return [folded[k] for k in sorted(folded)]


def _obligation_timeline(obs: list[dict]) -> list[dict]:
    events = []
    for ob in obs:
        if not isinstance(ob, dict):
            continue
        for h in ob.get("history", []):
            if isinstance(h, dict):
                events.append({"ts": h.get("ts", ""), "kind": "obligation", "ref": ob.get("id", ""),
                               "summary": f"{h.get('action', '?')}: {ob.get('claim', '')}",
                               "actor": h.get("by", "agent")})
    return events


_KIND_RANK = {"commit": 0, "tool_invocation": 1, "finding": 2, "obligation": 3, "note": 4}


def _order_timeline(events: list[dict]) -> list[dict]:
    return sorted(events, key=lambda e: (e.get("ts", ""), _KIND_RANK.get(e.get("kind", ""), 9), str(e.get("ref", ""))))


# --- assembly ------------------------------------------------------------------------

def collect(workspace: str, *, at: str | None = None, manifest_name: str = "manifest.json",
            template: str = DEFAULT_TEMPLATE, log_cap: int = DEFAULT_LOG_CAP,
            source_date: float | None = None, redact: bool = True) -> dict:
    """Assemble the Investigation Record (IR) for ``workspace``. Pure given the commit."""
    workspace = os.path.abspath(workspace)
    if not os.path.isdir(workspace):
        raise ReportError(f"no such workspace: {workspace}")
    man = _collect_manifest(workspace, manifest_name)
    git_summary, head, git_events = _collect_git(workspace, at, log_cap)
    findings = _collect_findings(workspace)
    try:
        ledger = obligations.load(workspace)
    except obligations.ObligationError as exc:
        raise ReportError(str(exc)) from exc
    tool_invocations = man.get("tool_invocations", []) if isinstance(man.get("tool_invocations"), list) else []

    checksums = man.get("checksums", []) if isinstance(man.get("checksums"), list) else []
    firmware_checksums = [
        {"path": c.get("path", ""), "sha256": c.get("sha256", ""), "size": c.get("size")}
        for c in checksums if isinstance(c, dict)
    ]

    timeline = _order_timeline(
        git_events + _tool_timeline(tool_invocations)
        + [{"ts": f.get("ts", ""), "kind": "finding", "ref": f.get("id", ""),
            "summary": f.get("title", ""), "actor": "agent"} for f in findings]
        + _obligation_timeline(ledger["obligations"])
    )

    commit = head or "no-git"
    report_id = "rpt-" + hashlib.sha256((commit + "|" + template).encode()).hexdigest()[:12]

    ir = {
        "report_meta": {
            "schema_version": SCHEMA_VERSION,
            "report_id": report_id,
            "template": template,
            "generator_version": GENERATOR_VERSION,
            "workspace_commit": head,
            "generated_at": _rfc3339(source_date),
            "source_date_epoch": int(source_date) if source_date is not None else int(_now_epoch()),
            "redacted": bool(redact),
        },
        "target": {
            "target_id": man.get("target_id", os.path.basename(workspace)),
            "class": man.get("class", "unknown"),
            "identity": man.get("identity", {}) if isinstance(man.get("identity"), dict) else {},
            "checksums": firmware_checksums,
            "hardware_bound": man.get("hardware_bound", []) if isinstance(man.get("hardware_bound"), list) else [],
        },
        "timeline": timeline,
        "tools_used": _fold_tools(tool_invocations),
        "firmware_checksums": firmware_checksums,
        "git_summary": git_summary,
        "findings": findings,
        "obligations": ledger["obligations"],
        "obligations_summary": obligations.summary(ledger),
    }
    if redact:
        ir = _mask_tree(ir)
        # report_meta is generator-authored, not target-derived; keep it verbatim.
        ir["report_meta"]["redacted"] = True
    return ir


# --- renderers -----------------------------------------------------------------------

def render_json(ir: dict) -> str:
    return json.dumps(ir, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _jinja_env(template_dir: str, autoescape: bool):
    from jinja2 import FileSystemLoader
    from jinja2.sandbox import SandboxedEnvironment

    env = SandboxedEnvironment(
        loader=FileSystemLoader(template_dir),
        autoescape=autoescape,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    if autoescape:
        # The template must not be able to opt a target-derived field out of escaping.
        env.filters.pop("safe", None)
    env.filters["sha_short"] = lambda s: (s or "")[:12]
    env.globals["SEVERITY_ORDER"] = ["critical", "high", "medium", "low", "info"]
    return env


def resolve_template(name: str, workspace: str | None, templates_dir: str | None) -> str:
    """Resolve a template NAME (or PATH) to a directory holding report.<fmt>.j2.

    Search order: a literal path, then the workspace's own report-templates/, then the
    site dir (``/etc/limpet/report-templates`` or ``--templates-dir``), then built-ins.
    First match wins. Raises ReportError if nothing resolves.
    """
    if os.path.sep in name or name.startswith("."):
        if os.path.isdir(name):
            return os.path.abspath(name)
        raise ReportError(f"template path not found: {name}")
    roots = []
    if workspace:
        roots.append(os.path.join(workspace, "report-templates"))
    roots.append(templates_dir or SITE_TEMPLATES_DIR)
    roots.append(BUILTIN_TEMPLATES_DIR)
    for root in roots:
        cand = os.path.join(root, name)
        if os.path.isdir(cand):
            return cand
    raise ReportError(f"template not found: {name} (searched {', '.join(roots)})")


def render_template(ir: dict, fmt: str, template_dir: str) -> str:
    fname = f"report.{fmt}.j2"
    if not os.path.exists(os.path.join(template_dir, fname)):
        raise ReportError(f"template {template_dir} declares no {fmt} ({fname} missing)")
    env = _jinja_env(template_dir, autoescape=(fmt == "html"))
    return env.get_template(fname).render(**ir)


ALL_FORMATS = ("md", "html", "json")


def render(ir: dict, fmt: str, template_dir: str) -> str:
    if fmt == "json":
        return render_json(ir)
    return render_template(ir, fmt, template_dir)


# --- top-level export ----------------------------------------------------------------

def export(workspace: str, *, formats=("md",), template: str = DEFAULT_TEMPLATE,
           out_dir: str | None = None, templates_dir: str | None = None,
           manifest_name: str = "manifest.json", at: str | None = None,
           log_cap: int = DEFAULT_LOG_CAP, source_date: float | None = None,
           redact: bool = True) -> dict:
    """Export ``workspace`` to the requested formats. Returns a status dict."""
    workspace = os.path.abspath(workspace)
    if "all" in formats:
        formats = ALL_FORMATS
    for fmt in formats:
        if fmt not in ALL_FORMATS:
            raise ReportError(f"unknown format: {fmt} (choose from {', '.join(ALL_FORMATS)})")

    ir = collect(workspace, at=at, manifest_name=manifest_name, template=template,
                 log_cap=log_cap, source_date=source_date, redact=redact)
    report_id = ir["report_meta"]["report_id"]

    tmpl_dir = None
    if any(f != "json" for f in formats):
        tmpl_dir = resolve_template(template, workspace, templates_dir)

    out = out_dir or os.path.join(workspace, "reports", report_id)
    os.makedirs(out, exist_ok=True)

    written = {}
    for fmt in formats:
        text = render(ir, fmt, tmpl_dir)
        path = os.path.join(out, f"report.{fmt}")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        written[fmt] = path
    return {"report_id": report_id, "workspace": workspace, "out_dir": out,
            "written": written, "redacted": ir["report_meta"]["redacted"],
            "template": template, "workspace_commit": ir["report_meta"]["workspace_commit"]}
