"""wq#3969 -- LIMPET per-workspace SQLite RE index (wq#3944 / A6).

SQL-as-universal-RE-query: a single read-only SQLite store beside the evidence so
the agent can query large reverse-engineering output (functions, strings, xrefs,
symbols) and the per-call invocation log without inlining megabytes into context.
This is the "optional workspace index, not the primary interface" adopted as A6 in
``design/wq3929_competitive_analysis.md`` §5, hung off the existing
``workspace.git``/``manifest.json`` model (``design/wq3806_limpet_architecture.md``
§5, :211-218).

Prior art (the ticket's mandatory FIRST STEP). Recon-2026 "SQL as a universal
disassembler query language" was *described*, not verified, when wq#3929 ran, and the
fleet web/github search backends are still down on 2026-10-15 (ISSUE-LOG #1325:
SearXNG engines CAPTCHA/rate-limited, no Brave key per wq#2595, GitHub 401 -- re-run
confirmed all four for this ticket). The one public RE tool whose SQLite schema *was*
verified from source is ``jtang613/GhidrAssist`` (README fetched 2026-10-15, recorded
in wq#3929 §9): "SQLite persistence" with "full-text search (FTS5) on summaries and
security annotations". We adopt that shape -- a plain relational store plus an FTS5
index on the string corpus -- rather than inventing one, and keep the table set the
ticket fixes: {functions, strings, xrefs, symbols, invocations}.

Layout::

    <workspace>/
      index.sqlite                      this index (rebuildable, derived)
      evidence/re-exports/*.json        tool exports (Ghidra/rizin), canonical shape below
      manifest.json                     tool_invocations -> the invocations table

Canonical RE-export file (``evidence/re-exports/<tool>.json``) -- the shape a
Ghidra/rizin wrapper writes, and what the committed test fixture uses::

    {
      "binary": "target/fw.bin",      optional default for every row in this file
      "source": "ghidra",             optional default producer label
      "functions": [{"addr": "0x1000", "name": "main", "size": 120}, ...],
      "strings":   [{"addr": "0x20a0", "value": "login failed", "encoding": "ascii"}, ...],
      "xrefs":     [{"from": "0x1000", "to": "0x20a0", "kind": "data"}, ...],
      "symbols":   [{"addr": "0x1000", "name": "main", "kind": "function"}, ...]
    }

Binary-derived free text (string values, symbol/function names, xref kinds) is scrubbed
on the way into this derived index (wq#4108, ``binary_scrub.scrub_text``): a crafted
string in the target is an indirect prompt-injection payload once the agent reads the
index. The raw evidence under ``evidence/re-exports/`` is the canonical copy and is never
rewritten.

``query_index`` enforces SELECT-only two ways (belt and suspenders): the connection is
opened read-only (``mode=ro``), and a sqlite authorizer denies every action that is not
a read, so a write/DDL/ATTACH/PRAGMA statement is refused up front with a clear error
rather than failing mid-execution. The module never executes anything from the
untrusted ``target/`` tree; it only reads JSON it is handed (fleet instinct: a planted
``json.py`` must not run on import).
"""
from __future__ import annotations

import json
import os
import sqlite3

import binary_scrub

# wq#4108: binary-derived free text (string values, symbol/function names, xref kinds) is
# attacker-controlled -- a crafted string in the target is an indirect prompt-injection
# payload once the agent reads this index. Scrub it on ingest into the derived, agent-
# facing index: redact known injection phrasing (reusing wq#4091's injection classifier),
# escape control/invisible chars, cap length. The canonical raw evidence under
# evidence/re-exports/ is never rewritten.


def _clean(value):
    """Scrub one untrusted binary-derived text field for safe inclusion in the index.

    Only actual strings are scrubbed; None and non-strings (e.g. an integer ``size``)
    pass through untouched. Addresses are coerced to str by the caller and are not free
    text, so they are not routed here.
    """
    return binary_scrub.scrub_text(value) if isinstance(value, str) else value

INDEX_NAME = "index.sqlite"
RE_EXPORTS_SUBDIR = os.path.join("evidence", "re-exports")
SCHEMA_VERSION = 1

# The fixed table set (ticket / wq#3929 §5 A6). Column lists are deliberately small and
# all-TEXT-or-INT so any tool export maps on without a migration; `source` records which
# tool/file a row came from so a query can tell a Ghidra function from a rizin one.
_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE functions (
    binary TEXT, addr TEXT, name TEXT, size INTEGER, source TEXT
);
CREATE TABLE strings (
    binary TEXT, addr TEXT, value TEXT, encoding TEXT, source TEXT
);
CREATE TABLE xrefs (
    binary TEXT, from_addr TEXT, to_addr TEXT, kind TEXT, source TEXT
);
CREATE TABLE symbols (
    binary TEXT, addr TEXT, name TEXT, kind TEXT, source TEXT
);
CREATE TABLE invocations (
    seq INTEGER, ts TEXT, plugin TEXT, version TEXT, trust TEXT,
    args_digest TEXT, summary TEXT
);
CREATE INDEX ix_functions_name ON functions(name);
CREATE INDEX ix_functions_addr ON functions(addr);
CREATE INDEX ix_strings_addr ON strings(addr);
CREATE INDEX ix_xrefs_to ON xrefs(to_addr);
CREATE INDEX ix_symbols_name ON symbols(name);
"""

# Adopted from GhidrAssist (above): FTS5 over the string corpus so the agent can do
# `SELECT ... FROM strings_fts WHERE strings_fts MATCH 'password'` instead of a LIKE scan.
# Best-effort: skipped if this sqlite build lacks FTS5 (the relational tables still work).
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE strings_fts USING fts5(value, addr UNINDEXED, binary UNINDEXED,
                                            source UNINDEXED, content='strings',
                                            content_rowid='rowid');
"""


class IndexError_(Exception):
    """A workspace that cannot be indexed, or a query that is refused."""


def index_path(workspace: str) -> str:
    return os.path.join(os.path.abspath(workspace), INDEX_NAME)


def _as_list(obj, key: str) -> list[dict]:
    val = obj.get(key)
    return [r for r in val if isinstance(r, dict)] if isinstance(val, list) else []


def _load_re_exports(workspace: str) -> list[dict]:
    """Read every ``evidence/re-exports/*.json`` as a parsed export object."""
    root = os.path.join(os.path.abspath(workspace), RE_EXPORTS_SUBDIR)
    if not os.path.isdir(root):
        return []
    exports = []
    for name in sorted(os.listdir(root)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(root, name)
        try:
            with open(path, encoding="utf-8") as fh:
                obj = json.load(fh)
        except (OSError, ValueError) as exc:
            raise IndexError_(f"bad RE export {name}: {exc}") from exc
        if not isinstance(obj, dict):
            raise IndexError_(f"RE export {name} is not a JSON object")
        obj.setdefault("_file", name)
        exports.append(obj)
    return exports


def _load_invocations(workspace: str, manifest_name: str) -> list[dict]:
    path = os.path.join(os.path.abspath(workspace), manifest_name)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            man = json.load(fh)
    except (OSError, ValueError) as exc:
        raise IndexError_(f"cannot read {manifest_name}: {exc}") from exc
    if not isinstance(man, dict):
        return []
    return [r for r in man.get("tool_invocations", []) if isinstance(r, dict)]


def _row_binary(export: dict, row: dict) -> str:
    return str(row.get("binary") or export.get("binary") or "")


def _row_source(export: dict, row: dict) -> str:
    return str(row.get("source") or export.get("source") or export.get("_file", ""))


def build_index(workspace: str, *, manifest_name: str = "manifest.json") -> dict:
    """(Re)build ``<workspace>/index.sqlite`` from the RE exports + manifest invocations.

    Idempotent: the DB is written to a temp file and atomically renamed, so a failed
    build never leaves a half-populated index. Returns per-table row counts.
    """
    workspace = os.path.abspath(workspace)
    if not os.path.isdir(workspace):
        raise IndexError_(f"no such workspace: {workspace}")

    exports = _load_re_exports(workspace)
    invocations = _load_invocations(workspace, manifest_name)

    dst = index_path(workspace)
    tmp = dst + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)

    conn = sqlite3.connect(tmp)
    counts = {"functions": 0, "strings": 0, "xrefs": 0, "symbols": 0, "invocations": 0}
    try:
        conn.executescript(_SCHEMA)
        has_fts = True
        try:
            conn.executescript(_FTS_SCHEMA)
        except sqlite3.OperationalError:
            has_fts = False  # sqlite built without FTS5; relational tables still usable

        for ex in exports:
            for r in _as_list(ex, "functions"):
                conn.execute(
                    "INSERT INTO functions(binary, addr, name, size, source) VALUES(?,?,?,?,?)",
                    (_row_binary(ex, r), str(r.get("addr", "")), _clean(r.get("name")),
                     r.get("size"), _row_source(ex, r)))
                counts["functions"] += 1
            for r in _as_list(ex, "strings"):
                conn.execute(
                    "INSERT INTO strings(binary, addr, value, encoding, source) VALUES(?,?,?,?,?)",
                    (_row_binary(ex, r), str(r.get("addr", "")), _clean(r.get("value")),
                     _clean(r.get("encoding")), _row_source(ex, r)))
                counts["strings"] += 1
            for r in _as_list(ex, "xrefs"):
                conn.execute(
                    "INSERT INTO xrefs(binary, from_addr, to_addr, kind, source) VALUES(?,?,?,?,?)",
                    (_row_binary(ex, r), str(r.get("from", "")), str(r.get("to", "")),
                     _clean(r.get("kind")), _row_source(ex, r)))
                counts["xrefs"] += 1
            for r in _as_list(ex, "symbols"):
                conn.execute(
                    "INSERT INTO symbols(binary, addr, name, kind, source) VALUES(?,?,?,?,?)",
                    (_row_binary(ex, r), str(r.get("addr", "")), _clean(r.get("name")),
                     _clean(r.get("kind")), _row_source(ex, r)))
                counts["symbols"] += 1

        for inv in invocations:
            conn.execute(
                "INSERT INTO invocations(seq, ts, plugin, version, trust, args_digest, summary)"
                " VALUES(?,?,?,?,?,?,?)",
                (inv.get("seq"), inv.get("ts"), inv.get("plugin"), inv.get("version"),
                 inv.get("trust"), inv.get("args_digest"), inv.get("summary")))
            counts["invocations"] += 1

        if has_fts and counts["strings"]:
            # Mirror the string corpus into the FTS index (external-content table).
            conn.execute("INSERT INTO strings_fts(rowid, value, addr, binary, source) "
                         "SELECT rowid, value, addr, binary, source FROM strings")

        conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)",
                     (str(SCHEMA_VERSION),))
        conn.execute("INSERT INTO meta(key, value) VALUES('fts', ?)",
                     ("1" if has_fts else "0",))
        conn.commit()
    finally:
        conn.close()

    os.replace(tmp, dst)
    return counts


# --- read-only query path ------------------------------------------------------------

# sqlite authorizer action codes we permit: a statement that triggers anything else is a
# write/DDL/ATTACH and must be refused. SELECT + READ cover ordinary queries;
# FUNCTION allows count()/length()/etc; RECURSIVE allows WITH RECURSIVE CTEs.
_ALLOWED_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", -1),
})


def _authorizer(action, arg1, arg2, _db, _trigger):
    # A value-less PRAGMA is a read (FTS5's constructor issues `PRAGMA data_version`);
    # a PRAGMA with a value (arg2) is a write/config change -> deny. The read-only
    # connection is the backstop either way.
    if action == sqlite3.SQLITE_PRAGMA:
        return sqlite3.SQLITE_OK if arg2 is None else sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def _looks_like_select(sql: str) -> bool:
    """Cheap first-keyword gate for a friendly error before the authorizer fires."""
    stripped = sql.lstrip()
    # drop a leading -- line comment or /* */ block comment
    while True:
        if stripped.startswith("--"):
            nl = stripped.find("\n")
            stripped = "" if nl < 0 else stripped[nl + 1:].lstrip()
        elif stripped.startswith("/*"):
            end = stripped.find("*/")
            stripped = "" if end < 0 else stripped[end + 2:].lstrip()
        else:
            break
    head = stripped[:6].lower()
    return head.startswith("select") or head.startswith("with")


def query_index(workspace: str, sql: str, *, params=(), max_rows: int = 10000) -> dict:
    """Run a read-only ``SELECT`` against the workspace index.

    Returns ``{"columns": [...], "rows": [[...], ...], "row_count": n,
    "truncated": bool}``. Raises :class:`IndexError_` if the index is missing or the
    statement is not a single read-only SELECT.
    """
    path = index_path(workspace)
    if not os.path.exists(path):
        raise IndexError_(f"no {INDEX_NAME} in {workspace}: run `build_index` first")
    if not sql or not sql.strip():
        raise IndexError_("empty query")
    if not _looks_like_select(sql):
        raise IndexError_("only SELECT queries are permitted (read-only index)")

    uri = "file:" + path + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.set_authorizer(_authorizer)
        try:
            cur = conn.execute(sql, params)
        except sqlite3.DatabaseError as exc:
            # Authorizer denials and "only execute one statement" both land here.
            raise IndexError_(f"query refused: {exc}") from exc
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return {"columns": columns, "rows": [list(r) for r in rows],
                "row_count": len(rows), "truncated": truncated}
    finally:
        conn.close()
