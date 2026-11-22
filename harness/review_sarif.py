"""review-readonly findings -> JSON + SARIF 2.1.0 in the ticket's run dir (wq#5145).

wq#5041 gap 5 (CodeAF's sec/review subharnesses): a review worker reads source and
reports; it never builds, tests, runs an exploit, posts or fixes. Under the
review-readonly permission pack (permission_profiles.render_review_readonly) it has
Read/Grep/Glob and nothing else, so it cannot write a file or run `wq done`. Its whole
deliverable is one fenced block at the end of its final message:

    ```review-findings
    {"summary": "...",
     "findings": [{"rule_id": "py-shell-injection", "level": "error",
                   "message": "...", "path": "harness/x.py",
                   "start_line": 12, "end_line": 14}]}
    ```

fworker parses that block from the session's result text, and this module writes
`review-findings.json` (the normalised doc) and `review.sarif` (SARIF 2.1.0, OASIS
errata01 schema) into the run dir. The harness then closes the ticket citing the SARIF
path. Consumers (mcp-guard, code audits, GitHub code scanning) read the SARIF file.

`parse_findings` is strict on structure and lenient on optional fields: a block that is
missing, not JSON, or has a finding with no message/path is a parse error and the
ticket is not closed (the ordinary FALSE_COMPLETE rework path takes over). An empty
findings list is valid: "reviewed, found nothing" is a real result.
"""
import json
import os
import re
import sys

ARCHETYPE = "review-readonly"
FENCE_TAG = "review-findings"
FINDINGS_FILENAME = "review-findings.json"
SARIF_FILENAME = "review.sarif"
SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = ("https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/"
                "sarif-schema-2.1.0.json")
TOOL_NAME = "foreman-review-readonly"
# SARIF 2.1.0 §3.27.10 result.level: exactly these four values.
LEVELS = ("none", "note", "warning", "error")
_LEVEL_ALIASES = {"critical": "error", "high": "error", "medium": "warning",
                  "low": "note", "info": "note", "informational": "note"}
MAX_FINDINGS = 500
_FENCE_RE = re.compile(r"```" + re.escape(FENCE_TAG) + r"[ \t]*\n(.*?)\n[ \t]*```", re.S)
_RULE_ID_RE = re.compile(r"[^A-Za-z0-9._/-]+")


class FindingsError(ValueError):
    pass


def extract_block(text):
    """Body of the LAST ```review-findings fence in text (a worker that rewrites its
    findings mid-message means the final one). None when there is no such fence."""
    if not isinstance(text, str):
        return None
    blocks = _FENCE_RE.findall(text)
    return blocks[-1] if blocks else None


def _norm_level(raw):
    lvl = str(raw or "warning").strip().lower()
    lvl = _LEVEL_ALIASES.get(lvl, lvl)
    if lvl not in LEVELS:
        raise FindingsError(f"level {raw!r} is not one of {LEVELS} (or a severity alias)")
    return lvl


def _norm_line(raw, field, idx):
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise FindingsError(f"finding {idx}: {field} {raw!r} is not an integer")
    if n < 1:
        raise FindingsError(f"finding {idx}: {field} {n} < 1 (SARIF lines are 1-based)")
    return n


def _norm_path(raw, idx):
    path = str(raw or "").strip()
    if not path:
        raise FindingsError(f"finding {idx}: path is required")
    # SARIF artifactLocation.uri: forward slashes, relative to the repo root.
    path = path.replace("\\", "/")
    if path.startswith("./"):
        path = path[2:]
    return path


def normalise(doc):
    """Validate and normalise a parsed findings doc. Returns a new dict:
    {"summary": str, "findings": [{rule_id, level, message, path, start_line,
    end_line}]}."""
    if not isinstance(doc, dict):
        raise FindingsError("findings block must be a JSON object")
    raw = doc.get("findings")
    if not isinstance(raw, list):
        raise FindingsError("'findings' must be a list (use [] for a clean review)")
    if len(raw) > MAX_FINDINGS:
        raise FindingsError(f"{len(raw)} findings > cap {MAX_FINDINGS}")
    out = []
    for i, f in enumerate(raw):
        if not isinstance(f, dict):
            raise FindingsError(f"finding {i}: not an object")
        message = str(f.get("message") or "").strip()
        if not message:
            raise FindingsError(f"finding {i}: message is required")
        rule_id = _RULE_ID_RE.sub("-", str(f.get("rule_id") or "review-finding").strip()) \
            or "review-finding"
        start = _norm_line(f.get("start_line"), "start_line", i)
        end = _norm_line(f.get("end_line"), "end_line", i)
        if start is None and end is not None:
            start = end
        if start is not None and end is not None and end < start:
            raise FindingsError(f"finding {i}: end_line {end} < start_line {start}")
        out.append({"rule_id": rule_id, "level": _norm_level(f.get("level")),
                    "message": message, "path": _norm_path(f.get("path"), i),
                    "start_line": start, "end_line": end})
    return {"summary": str(doc.get("summary") or "").strip(), "findings": out}


def parse_findings(text):
    """Result text -> normalised findings doc. Raises FindingsError."""
    body = extract_block(text)
    if body is None:
        raise FindingsError(f"no ```{FENCE_TAG} fenced block in the final message")
    try:
        doc = json.loads(body)
    except ValueError as e:
        raise FindingsError(f"{FENCE_TAG} block is not valid JSON: {e}")
    return normalise(doc)


def to_sarif(findings_doc, ticket_id=None, repo=None):
    """Normalised findings doc -> SARIF 2.1.0 log (dict)."""
    rules, rule_index = [], {}
    results = []
    for f in findings_doc.get("findings", []):
        rid = f["rule_id"]
        if rid not in rule_index:
            rule_index[rid] = len(rules)
            rules.append({"id": rid, "shortDescription": {"text": rid}})
        region = {}
        if f.get("start_line"):
            region["startLine"] = f["start_line"]
            if f.get("end_line"):
                region["endLine"] = f["end_line"]
        phys = {"artifactLocation": {"uri": f["path"], "uriBaseId": "SRCROOT"}}
        if region:
            phys["region"] = region
        results.append({"ruleId": rid, "ruleIndex": rule_index[rid], "level": f["level"],
                        "message": {"text": f["message"]},
                        "locations": [{"physicalLocation": phys}]})
    run = {"tool": {"driver": {"name": TOOL_NAME, "rules": rules}},
           "results": results}
    props = {}
    if ticket_id is not None:
        props["ticket"] = f"wq#{ticket_id}"
    if findings_doc.get("summary"):
        props["summary"] = findings_doc["summary"]
    if repo:
        props["repo"] = repo
        run["originalUriBaseIds"] = {"SRCROOT": {"uri": "file://" + os.path.normpath(repo) + "/"}}
    if props:
        run["properties"] = props
    return {"$schema": SARIF_SCHEMA, "version": SARIF_VERSION, "runs": [run]}


def _write_private(path, doc):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def emit(result_text, out_dir, ticket_id=None, repo=None):
    """Parse result_text and write review-findings.json + review.sarif under out_dir.
    Returns {"sarif": path, "json": path, "count": n, "levels": {level: n}}.
    Raises FindingsError on a missing/malformed block (nothing is written then)."""
    doc = parse_findings(result_text)
    sarif = to_sarif(doc, ticket_id=ticket_id, repo=repo)
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, FINDINGS_FILENAME)
    sarif_path = os.path.join(out_dir, SARIF_FILENAME)
    _write_private(json_path, dict(doc, ticket=(f"wq#{ticket_id}" if ticket_id else None)))
    _write_private(sarif_path, sarif)
    levels = {}
    for f in doc["findings"]:
        levels[f["level"]] = levels.get(f["level"], 0) + 1
    return {"sarif": sarif_path, "json": json_path, "count": len(doc["findings"]),
            "levels": levels}


def main(argv=None):
    """CLI: review_sarif.py <result-text-file> <out-dir> [--ticket N] [--repo PATH].
    Prints the emit() summary as JSON; exit 2 on a findings error."""
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("result_file")
    ap.add_argument("out_dir")
    ap.add_argument("--ticket")
    ap.add_argument("--repo")
    a = ap.parse_args(argv)
    with open(a.result_file) as f:
        text = f.read()
    try:
        out = emit(text, a.out_dir, ticket_id=a.ticket, repo=a.repo)
    except FindingsError as e:
        print(f"review_sarif: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
