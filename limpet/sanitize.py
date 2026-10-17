"""wq#4091 -- sanitization gate between a workspace's raw evidence and the agent core.

Companion to ``injection.py`` (the pure classifier) and the closing half of the
pattern ``design/wq3999_rex_skill_dissection.md`` section 3 adopts from REx@Skill's
``scripts/sanitize.py``: walk every artefact an agent may read, redact any hit to
``injection.REDACTION_TOKEN`` *before* the first agent read, and file the original
under ``<workspace>/quarantine/`` so nothing is destroyed, only kept out of context.

Scope, translated from REx's ``decomp/disasm/meta/dynamic`` subdirs onto Limpet's real
workspace layout (``workspace_index.RE_EXPORTS_SUBDIR``, ``workspace_console.
EVIDENCE_LOG_RELPATH`` -- both pre-existing, not invented here):

  evidence/re-exports/*.json   tool-export JSON (``workspace_index.py``'s canonical
                                shape) -- every string leaf is attacker-reachable:
                                recovered strings, and function/symbol names a
                                stripped-binary heuristic may pull straight off the
                                image.
  evidence/**/*.txt            plain recovered-strings dumps (one string per line,
                                the shape ``test_critic.py``'s own fixture already
                                uses at ``evidence/strings/hits.txt``).
  evidence/tool-calls.jsonl    the meta-server's per-call evidence log
                                (``workspace_console.EVIDENCE_LOG_RELPATH``); only its
                                ``coverage``/``error`` fields are scanned, because
                                those are the free-text fields a child tool or child
                                process can influence -- ``error`` is set from
                                ``str(exc)`` on a ``CliToolError``/``ChildServerError``,
                                which for the latter embeds the child's raw stderr
                                (``stdio_mcp_client.py``'s ``_stderr_text()``) -- the
                                rest of a block (``tool``, ``ts``, ``ok``,
                                ``evidence_id``) is meta-server-authored, not
                                target-derived.

Findings (``evidence/findings/*.json``) are deliberately **not** scanned: those are
the analyst's own committed conclusions, not raw target output, and redacting them
would hide the analyst's words rather than the target's.

JSON artefacts are re-parsed and rewritten structurally (walk the parsed object,
redact string leaves, re-serialize) -- never a line-based text substitution, which
the dissection calls out by name as the mistake REx's own prior version made ("a
prior line-based version turned 35 dynamic JSONs invalid",
``design/wq3999_rex_skill_dissection.md:99-100``). This mirrors
``workspace_report._mask_tree``'s walk exactly, for the same reason: a regex over raw
JSON text can turn a quoted ``"`` into broken syntax; a regex over an already-parsed
Python string value cannot.

Fail-closed gate contract (CLI only; this module makes no network/process call and
enforces nothing by itself -- a caller, e.g. the meta-server wq#3808 builds, enforces
the gate by checking the exit code):

  exit 0   clean -- nothing matched a known injection shape.
  exit 1   scrubbed -- one or more hits were redacted in place. The artefacts on disk
           are already safe to hand to the agent core; exit 1 is the audit signal, not
           a failure the caller must retry.
  exit 2   error -- not a Limpet workspace, or an artefact that could not be parsed/
           rewritten. Nothing was handed to the agent core; the caller must not proceed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import injection

QUARANTINE_RELPATH = os.path.join("quarantine", "redactions.jsonl")
RE_EXPORTS_SUBDIR = os.path.join("evidence", "re-exports")
TOOL_CALLS_RELPATH = os.path.join("evidence", "tool-calls.jsonl")
_SCANNED_LOG_FIELDS = ("coverage", "error")


class SanitizeError(Exception):
    """A workspace that cannot be sanitized (missing, or an artefact that won't parse)."""


def _quarantine_entry(artifact: str, locator: str, layer: str, original: str) -> dict:
    return {"artifact": artifact, "locator": locator, "layer": layer, "original": original}


def _sanitize_tree(obj, artifact: str, locator: str, entries: list):
    """Walk a parsed JSON value, redacting string leaves that classify as injection.

    Returns the (possibly rewritten) value. Mirrors ``workspace_report._mask_tree``'s
    recursion shape so the two defenses read as one system.
    """
    if isinstance(obj, str):
        layer = injection.classify_normalized(obj)
        if layer is None:
            return obj
        entries.append(_quarantine_entry(artifact, locator, layer, obj))
        return injection.REDACTION_TOKEN
    if isinstance(obj, list):
        return [_sanitize_tree(v, artifact, f"{locator}[{i}]", entries)
                for i, v in enumerate(obj)]
    if isinstance(obj, dict):
        return {k: _sanitize_tree(v, artifact, f"{locator}.{k}", entries)
                for k, v in obj.items()}
    return obj


def _atomic_write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _sanitize_json_file(path: str, artifact: str, entries: list) -> bool:
    """Sanitize one JSON artefact in place. Returns True iff it was rewritten."""
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SanitizeError(f"cannot parse {artifact}: {exc}") from exc
    before = len(entries)
    cleaned = _sanitize_tree(obj, artifact, "$", entries)
    if len(entries) == before:
        return False
    _atomic_write(path, json.dumps(cleaned, indent=2, sort_keys=True) + "\n")
    return True


def _sanitize_txt_file(path: str, artifact: str, entries: list) -> bool:
    """Sanitize a plain recovered-strings dump, one line at a time. Line-based
    rewriting is safe here (unlike JSON) because each line has no surrounding
    syntax a redaction could break.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        raise SanitizeError(f"cannot read {artifact}: {exc}") from exc
    changed = False
    out = []
    for i, line in enumerate(lines):
        stripped = line[:-1] if line.endswith("\n") else line
        layer = injection.classify_normalized(stripped)
        if layer is None:
            out.append(line)
            continue
        entries.append(_quarantine_entry(artifact, f"L{i + 1}", layer, stripped))
        out.append(injection.REDACTION_TOKEN + ("\n" if line.endswith("\n") else ""))
        changed = True
    if changed:
        _atomic_write(path, "".join(out))
    return changed


def _sanitize_tool_calls_log(path: str, artifact: str, entries: list) -> bool:
    """Sanitize only the child-controlled free-text fields of each evidence block."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw_lines = fh.readlines()
    except OSError as exc:
        raise SanitizeError(f"cannot read {artifact}: {exc}") from exc
    changed = False
    out = []
    for i, raw in enumerate(raw_lines):
        stripped = raw.strip()
        if not stripped:
            out.append(raw)
            continue
        try:
            block = json.loads(stripped)
        except ValueError:
            out.append(raw)  # a torn/partial append -- leave it, matches workspace_console
            continue
        if not isinstance(block, dict):
            out.append(raw)
            continue
        hit = False
        for field in _SCANNED_LOG_FIELDS:
            val = block.get(field)
            if not isinstance(val, str):
                continue
            layer = injection.classify_normalized(val)
            if layer is None:
                continue
            entries.append(_quarantine_entry(artifact, f"L{i + 1}.{field}", layer, val))
            block[field] = injection.REDACTION_TOKEN
            hit = True
        if hit:
            changed = True
            out.append(json.dumps(block, sort_keys=True) + "\n")
        else:
            out.append(raw)
    if changed:
        _atomic_write(path, "".join(out))
    return changed


def _find_txt_files(evidence_dir: str) -> list:
    found = []
    for root, _dirs, names in os.walk(evidence_dir):
        for name in sorted(names):
            if name.endswith(".txt"):
                found.append(os.path.join(root, name))
    return sorted(found)


def sanitize_workspace(workspace: str) -> dict:
    """Run the sanitize pass over one workspace's raw evidence. Idempotent: a
    re-run over already-redacted artefacts finds nothing (the token itself matches
    no pattern). Returns ``{"scrubbed": int, "entries": [...]}``.

    Raises ``SanitizeError`` on a missing workspace or an artefact that cannot be
    parsed/rewritten -- callers must treat that as exit 2 (block), never a partial
    pass.
    """
    workspace = os.path.abspath(workspace)
    if not os.path.isdir(workspace):
        raise SanitizeError(f"no such workspace: {workspace}")

    entries: list = []

    re_exports = os.path.join(workspace, RE_EXPORTS_SUBDIR)
    if os.path.isdir(re_exports):
        for name in sorted(os.listdir(re_exports)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(re_exports, name)
            artifact = os.path.join(RE_EXPORTS_SUBDIR, name)
            _sanitize_json_file(path, artifact, entries)

    evidence_dir = os.path.join(workspace, "evidence")
    if os.path.isdir(evidence_dir):
        for path in _find_txt_files(evidence_dir):
            artifact = os.path.relpath(path, workspace)
            _sanitize_txt_file(path, artifact, entries)

    tool_calls = os.path.join(workspace, TOOL_CALLS_RELPATH)
    if os.path.isfile(tool_calls):
        _sanitize_tool_calls_log(tool_calls, TOOL_CALLS_RELPATH, entries)

    if entries:
        quarantine_path = os.path.join(workspace, QUARANTINE_RELPATH)
        os.makedirs(os.path.dirname(quarantine_path), exist_ok=True)
        with open(quarantine_path, "a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")

    return {"scrubbed": len(entries), "entries": entries}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="limpet-sanitize",
        description="Scan a Limpet workspace's raw evidence for model-directed "
                     "injection and redact it before the agent core reads it.")
    parser.add_argument("workspace")
    parser.add_argument("--json", action="store_true", help="emit the result as JSON")
    args = parser.parse_args(argv)

    try:
        result = sanitize_workspace(args.workspace)
    except SanitizeError as exc:
        print(f"sanitize: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif result["scrubbed"]:
        print(f"sanitize: redacted {result['scrubbed']} hit(s), "
              f"originals filed under {QUARANTINE_RELPATH}")
    else:
        print("sanitize: clean")

    return 1 if result["scrubbed"] else 0


if __name__ == "__main__":
    sys.exit(main())
