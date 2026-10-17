"""wq#4091 -- quarantine/sanitize gate between tool output and agent context.

Closes the gap flagged TOP of ``design/wq3999_rex_skill_dissection.md`` §3: a
fleet grep found no quarantine/sanitize/injection-detection primitive in
``limpet/`` before this change -- the existing untrusted-input hygiene
(``design/wq3806_limpet_architecture.md`` §5) defends against *code*
execution from a planted file, not against *text* inside the target aimed at
the analysing model. Target binary strings (banners, decompiled literals,
error messages) are hostile input to the agent reading them; this module is
the sanitization layer standing between a workspace's tool output
(``evidence/``, ``notes/``) and the agent's context window.

Ported from REx@Skill's ``scripts/sanitize.py`` (dissection §3): walk every
artefact an agent may read, redact each ``injection.is_injection`` hit to
``REDACTION_MARKER`` *before* any agent reads it, and file the untouched
original under ``<workspace>/quarantine/`` so an operator can audit what was
scrubbed without the agent ever seeing the raw hit.

JSON artefacts are rewritten *structurally* -- parsed, walked leaf-by-leaf,
re-serialized -- never line-by-line: REx's own lesson (dissection §3) is that
a line-based pass on JSON output corrupted 35 files into invalid JSON. Plain
text artefacts (strings dumps, logs) are redacted per line, which is the
natural atom for that format (one recovered string per line).

Errs toward quarantine (REx's asymmetry, dissection §3): a scrubbed legitimate
string costs a little context; a leaked injection costs the integrity of the
whole run. ``sanitize_workspace`` is meant to run as a fail-closed gate -- a
caller (the meta-server, a pipeline step) should refuse to hand a workspace's
evidence to the agent core until this has run; the CLI (``workspace_cli.py
sanitize``) exits 1 when it scrubbed something so a pipeline can branch on it,
matching REx's own exit convention.
"""
from __future__ import annotations

import datetime as _dt
import json
import os

import injection

REDACTION_MARKER = "<<REDACTED-UNTRUSTED-EMBEDDED-STRING>>"
AGENT_READABLE_DIRS = ("evidence", "notes")
QUARANTINE_DIR = "quarantine"
REPORT_NAME = "report.json"


def _now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _redact_json(obj):
    """Walk a parsed JSON value, redacting any string leaf that matches. Returns (new_obj, hits)."""
    if isinstance(obj, dict):
        hits = 0
        new = {}
        for key, value in obj.items():
            new_value, h = _redact_json(value)
            new[key] = new_value
            hits += h
        return new, hits
    if isinstance(obj, list):
        hits = 0
        new = []
        for value in obj:
            new_value, h = _redact_json(value)
            new.append(new_value)
            hits += h
        return new, hits
    if isinstance(obj, str) and injection.is_injection(obj):
        return REDACTION_MARKER, 1
    return obj, 0


def _redact_text(text: str):
    """Redact per-line (one recovered string per line, the `strings`-dump atom)."""
    hits = 0
    out_lines = []
    for line in text.splitlines(keepends=True):
        core = line.rstrip("\r\n")
        ending = line[len(core):]
        if core and injection.is_injection(core):
            out_lines.append(REDACTION_MARKER + ending)
            hits += 1
        else:
            out_lines.append(line)
    return "".join(out_lines), hits


def sanitize_file(path: str) -> dict:
    """Sanitize one artefact in place. Returns {"hits": int, "original": str|None}.

    ``original`` is the pre-redaction content, for the caller to quarantine --
    None when nothing was scrubbed (file left untouched) or the file could not
    be read as text (binary artefact, skipped rather than guessed at).
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            original = fh.read()
    except (OSError, UnicodeDecodeError):
        return {"hits": 0, "original": None}

    if path.endswith(".json"):
        try:
            parsed = json.loads(original)
        except ValueError:
            parsed = None
        if parsed is not None:
            redacted, hits = _redact_json(parsed)
            if hits:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(redacted, fh, indent=2, sort_keys=True)
                    fh.write("\n")
                return {"hits": hits, "original": original}
            return {"hits": 0, "original": None}

    new_text, hits = _redact_text(original)
    if hits:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        return {"hits": hits, "original": original}
    return {"hits": 0, "original": None}


def _quarantine_original(workspace: str, rel_path: str, original: str) -> None:
    dest = os.path.join(workspace, QUARANTINE_DIR, rel_path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(original)


def sanitize_workspace(workspace: str, dirs=AGENT_READABLE_DIRS) -> dict:
    """Sanitize every agent-readable artefact under ``workspace``.

    Walks ``dirs`` (default: ``evidence/``, ``notes/`` -- the agent-readable
    subdirs of a Limpet workspace, ``design/wq3806_limpet_architecture.md``
    §5), skipping ``quarantine/`` itself. Each file with a hit is redacted in
    place and its pre-redaction content filed under
    ``<workspace>/quarantine/<relpath>``. Writes
    ``<workspace>/quarantine/report.json`` recording what was scrubbed, and
    returns the same report.
    """
    root = os.path.abspath(workspace)
    quarantine_root = os.path.join(root, QUARANTINE_DIR)
    scrubbed = []
    files_scanned = 0
    for d in dirs:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            if os.path.commonpath([dirpath, quarantine_root]) == quarantine_root:
                continue
            for name in filenames:
                path = os.path.join(dirpath, name)
                files_scanned += 1
                result = sanitize_file(path)
                if result["original"] is not None:
                    rel = os.path.relpath(path, root)
                    _quarantine_original(root, rel, result["original"])
                    scrubbed.append({"path": rel, "hits": result["hits"]})

    report = {
        "schema_version": "1",
        "generated_at": _now(),
        "dirs": list(dirs),
        "files_scanned": files_scanned,
        "scrubbed": scrubbed,
        "hit_count": sum(s["hits"] for s in scrubbed),
    }
    os.makedirs(quarantine_root, exist_ok=True)
    with open(os.path.join(quarantine_root, REPORT_NAME), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return report
