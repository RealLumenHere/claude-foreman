#!/usr/bin/env python3
"""schema_parse.py — expected D1 schema from mailbot/migrations/*.sql (wq#1252).

The 2026-09-02 incident: a deploy shipped code writing `messages.dodge_style`
while remote D1 still had the pre-0002 schema. storeMessage() swallows D1
errors (mailbot/src/worker.js:292), so every write failed silently for ~7.5h.

Detecting that means comparing what the migrations say the schema should be
against what PRAGMA table_info() says it is. This module owns the first half:
a deliberately small SQL reader that understands only the two statements the
mailbot migrations actually use — CREATE TABLE and ALTER TABLE ADD COLUMN.
Anything else (indexes, triggers) is ignored rather than half-parsed, because
a wrong "expected" set pages Frank for nothing.
"""
import os
import re

# Column-list entries that are table constraints, not columns.
_CONSTRAINT_KEYWORDS = {
    "primary", "foreign", "unique", "check", "constraint", "exclude",
}


def strip_comments(sql):
    """Drop -- line comments and /* */ block comments."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _split_top_level(body):
    """Split a CREATE TABLE column list on commas that are not inside parens."""
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def _match_paren_body(sql, open_idx):
    """Return the text between the paren at open_idx and its match, or None."""
    depth = 0
    for i in range(open_idx, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[open_idx + 1:i]
    return None


def parse_sql(sql):
    """{table: set(columns)} from one SQL string.

    Only CREATE TABLE and ALTER TABLE ... ADD COLUMN contribute. Quoting styles
    ("x", `x`, [x]) are normalised away so the result compares cleanly against
    PRAGMA table_info output.
    """
    sql = strip_comments(sql)
    tables = {}

    for m in re.finditer(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\"`\[]?\w+[\"`\]]?)\s*\(",
        sql, re.I,
    ):
        body = _match_paren_body(sql, m.end() - 1)
        if body is None:
            continue
        table = _unquote(m.group(1))
        cols = tables.setdefault(table, set())
        for entry in _split_top_level(body):
            entry = entry.strip()
            if not entry:
                continue
            first = entry.split()[0]
            if first.lower().strip("(") in _CONSTRAINT_KEYWORDS:
                continue
            cols.add(_unquote(first))

    for m in re.finditer(
        r"ALTER\s+TABLE\s+([\"`\[]?\w+[\"`\]]?)\s+ADD\s+(?:COLUMN\s+)?([\"`\[]?\w+[\"`\]]?)",
        sql, re.I,
    ):
        tables.setdefault(_unquote(m.group(1)), set()).add(_unquote(m.group(2)))

    return tables


def _unquote(tok):
    return tok.strip().strip('"').strip("`").strip("[]").strip()


def expected_schema(migrations_dir):
    """Union of every *.sql in migrations_dir, applied in filename order.

    Filename order is the same order `wrangler d1 migrations apply` uses, which
    matters because 0002 adds a column to a table 0001 creates.
    """
    tables = {}
    for name in sorted(os.listdir(migrations_dir)):
        if not name.endswith(".sql"):
            continue
        with open(os.path.join(migrations_dir, name)) as f:
            for table, cols in parse_sql(f.read()).items():
                tables.setdefault(table, set()).update(cols)
    return tables


def diff_schema(expected, actual):
    """(missing_tables, missing_columns) — what the migrations promise but the
    live database does not have. Extra live tables/columns are NOT reported:
    a column the migrations no longer mention is not an outage, and paging on
    it would train Frank to ignore this check."""
    missing_tables = sorted(t for t in expected if t not in actual)
    missing_columns = sorted(
        f"{t}.{c}"
        for t, cols in expected.items()
        if t in actual
        for c in cols
        if c not in actual[t]
    )
    return missing_tables, missing_columns


def pending_migrations(migrations_dir, missing_tables, missing_columns):
    """Migration files that would supply the missing tables/columns, in apply
    order. The heal applies these and only these — not `migrations apply`
    blind, so a migration unrelated to the gap never runs as a side effect."""
    missing = set(missing_tables) | set(missing_columns)
    out = []
    for name in sorted(os.listdir(migrations_dir)):
        if not name.endswith(".sql"):
            continue
        with open(os.path.join(migrations_dir, name)) as f:
            supplied = parse_sql(f.read())
        names = set(supplied) | {
            f"{t}.{c}" for t, cols in supplied.items() for c in cols
        }
        if names & missing:
            out.append(name)
    return out
