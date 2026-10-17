"""wq#3969 -- tests for the per-workspace SQLite RE index (A6).

Builds a throwaway workspace in tmp, populates it from a *committed* RE-export
fixture (``tests/fixtures/re-export-ghidra.json`` -- rev-pinned, re-runnable
evidence, not a live mutable file) plus a manifest tool-invocation log, builds the
index, then checks: every table populated with the expected rows, SELECT returns the
expected rows (including an FTS string search and a join), and -- the falsifiable
probe (wq#2630) -- that each non-SELECT shape (INSERT/UPDATE/DELETE/DROP/CREATE/
ATTACH/PRAGMA write / two-statement smuggle) is REFUSED, so a broken read-only gate
actually fails this test instead of silently passing.
"""
import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workspace_index as wi  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "tests", "fixtures", "re-export-ghidra.json")
INJECTION_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "tests", "fixtures", "re-export-injection.json")


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / wi.RE_EXPORTS_SUBDIR).mkdir(parents=True)
    shutil.copy(FIXTURE, ws / wi.RE_EXPORTS_SUBDIR / "ghidra.json")
    (ws / "manifest.json").write_text(json.dumps({
        "target_id": "acme-router-v2",
        "tool_invocations": [
            {"seq": 1, "ts": "2026-01-01T01:00:00Z", "plugin": "ghidra", "version": "11.1",
             "trust": "core", "summary": "headless export"},
            {"seq": 2, "ts": "2026-01-01T02:00:00Z", "plugin": "rizin", "version": "0.7",
             "trust": "core", "summary": "strings pass"},
        ],
    }))
    return str(ws)


def test_build_populates_every_table(workspace):
    counts = wi.build_index(workspace)
    assert counts == {"functions": 3, "strings": 3, "xrefs": 3, "symbols": 3, "invocations": 2}
    assert os.path.exists(wi.index_path(workspace))


def test_select_returns_expected_rows(workspace):
    wi.build_index(workspace)

    res = wi.query_index(workspace, "SELECT name, size FROM functions ORDER BY addr")
    assert res["columns"] == ["name", "size"]
    assert res["rows"] == [["main", 240], ["check_password", 96], ["reboot_handler", 48]]
    assert res["row_count"] == 3 and not res["truncated"]

    # source/binary defaults propagate from the export header onto every row.
    one = wi.query_index(workspace, "SELECT binary, source FROM functions LIMIT 1")
    assert one["rows"][0] == ["target/fw.bin", "ghidra"]

    # the invocation log landed in its own table.
    inv = wi.query_index(workspace, "SELECT plugin FROM invocations ORDER BY seq")
    assert [r[0] for r in inv["rows"]] == ["ghidra", "rizin"]

    # a join across tables: which function references the string at 0x0020c0?
    j = wi.query_index(workspace,
                       "SELECT f.name FROM xrefs x JOIN functions f ON f.addr = x.from_addr "
                       "WHERE x.to_addr = '0x0020c0'")
    assert [r[0] for r in j["rows"]] == ["check_password"]


def test_fts_string_search(workspace):
    wi.build_index(workspace)
    meta = wi.query_index(workspace, "SELECT value FROM meta WHERE key = 'fts'")
    if meta["rows"][0][0] != "1":
        pytest.skip("sqlite built without FTS5")
    res = wi.query_index(workspace, "SELECT addr FROM strings_fts WHERE strings_fts MATCH 'password'")
    assert [r[0] for r in res["rows"]] == ["0x0020c0"]


@pytest.mark.parametrize("sql", [
    "INSERT INTO functions(name) VALUES('evil')",
    "UPDATE functions SET name = 'x'",
    "DELETE FROM functions",
    "DROP TABLE functions",
    "CREATE TABLE t (x)",
    "ALTER TABLE functions ADD COLUMN y",
    "PRAGMA user_version = 5",
    "ATTACH DATABASE '/tmp/evil.db' AS evil",
    "SELECT 1; DROP TABLE functions",   # two-statement smuggle
    "   -- comment\n DELETE FROM strings",
])
def test_non_select_refused(workspace, sql):
    wi.build_index(workspace)
    with pytest.raises(wi.IndexError_):
        wi.query_index(workspace, sql)
    # and the table is untouched: the read-only DB really blocked the write.
    after = wi.query_index(workspace, "SELECT COUNT(*) FROM functions")
    assert after["rows"][0][0] == 3


def test_empty_and_missing(workspace):
    with pytest.raises(wi.IndexError_):
        wi.query_index(workspace, "SELECT 1")      # index not built yet
    wi.build_index(workspace)
    with pytest.raises(wi.IndexError_):
        wi.query_index(workspace, "   ")           # empty query


def test_cli_build_and_query(workspace, capsys):
    import workspace_cli as wc
    assert wc.main(["index", "build", workspace]) == 0
    capsys.readouterr()  # drop the build's plain-text output before the --json query
    assert wc.main(["--json", "index", "query", workspace,
                    "SELECT name FROM functions ORDER BY addr"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and [r[0] for r in out["rows"]] == ["main", "check_password", "reboot_handler"]
    # the meta-server tool refuses a non-SELECT with a non-zero exit, no traceback.
    assert wc.main(["index", "query", workspace, "DROP TABLE functions"]) == 2


def test_binary_content_scrubbed_into_index(tmp_path):
    """wq#4108 falsifiable probe: a crafted target binary's strings/symbols reach the
    agent-facing index scrubbed -- injection phrasing redacted, control/invisible bytes
    escaped -- not verbatim. A no-op ingest fails this test.

    Input is a committed, rev-pinned malicious RE export (re-export-injection.json), not
    a live mutable file -- re-runnable evidence.
    """
    import injection
    ws = tmp_path / "evil-ws"
    (ws / wi.RE_EXPORTS_SUBDIR).mkdir(parents=True)
    shutil.copy(INJECTION_FIXTURE, ws / wi.RE_EXPORTS_SUBDIR / "evil.json")
    wi.build_index(str(ws))

    vals = [r[0] for r in wi.query_index(str(ws), "SELECT value FROM strings")["rows"]]
    for v in vals:
        assert "Ignore previous instructions" not in v   # phrasing redacted
        assert "\x00" not in v and "\x1b" not in v        # control bytes escaped
        assert "\n" not in v                              # no raw newline survived
    assert any(injection.REDACTION_TOKEN in v for v in vals)   # redaction happened
    assert any("\\x00" in v for v in vals)                     # control escaped, not dropped
    assert any("plain version banner v1.2.3" == v for v in vals)   # benign string intact

    sym = wi.query_index(str(ws), "SELECT name FROM symbols")["rows"][0][0]
    assert chr(0x202E) not in sym and "\x00" not in sym and "\\u202e" in sym


def test_rebuild_is_idempotent(workspace):
    wi.build_index(workspace)
    first = wi.query_index(workspace, "SELECT COUNT(*) FROM strings")["rows"][0][0]
    wi.build_index(workspace)                        # rebuild over existing index
    second = wi.query_index(workspace, "SELECT COUNT(*) FROM strings")["rows"][0][0]
    assert first == second == 3
