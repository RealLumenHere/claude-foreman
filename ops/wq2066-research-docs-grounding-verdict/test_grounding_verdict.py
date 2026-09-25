#!/usr/bin/env python3
"""Tests for grounding_verdict.py + patch_index_research.py (wq#2066). No live DB and
no live fleet_grounding: `check` is injected, the connection is a fake.
"""
import ast
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grounding_verdict as gv
import patch_index_research as pir


def fake_check(text, prompt_text="", check_urls=True):
    assert check_urls is False
    if "/opt/lumen/" in text:
        return {"verdict": "fail", "unresolved": [("path", "/opt/lumen/x")]}
    if len(text) < 20:
        return {"verdict": "fail", "unresolved": [("format", "under 200 chars")]}
    return {"verdict": "pass", "unresolved": []}


class ParseHeaderTests(unittest.TestCase):
    def test_fail_with_unresolved(self):
        h = "grounding: fail (fleet_unresolved=3, urls=2, urls_unseen=0)\n# T\nbody"
        self.assertEqual(gv.parse_header(h), ("fail", True))

    def test_pass_zero_unresolved(self):
        self.assertEqual(gv.parse_header("grounding: pass (fleet_unresolved=0, urls=0, urls_unseen=0)\nx"),
                         ("pass", False))

    def test_verdict_without_count(self):
        self.assertEqual(gv.parse_header("grounding: fail\nx"), ("fail", None))

    def test_no_header_is_unknown(self):
        self.assertEqual(gv.parse_header("# just a report\n"), (None, None))
        self.assertEqual(gv.parse_header(None), (None, None))

    def test_header_quoted_deep_in_body_ignored(self):
        body = "# T\n" + "line\n" * 60 + "grounding: pass (fleet_unresolved=0)\n"
        self.assertEqual(gv.parse_header(body), (None, None))

    def test_unresolved_line_is_not_a_verdict(self):
        self.assertEqual(gv.parse_header("grounding_unresolved: path:/opt/lumen/x\n"), (None, None))


class AssessTests(unittest.TestCase):
    def test_fleet_claim_sets_flag(self):
        self.assertEqual(gv.assess("see /opt/lumen/x " * 5, fake_check), ("fail", True))

    def test_format_only_fail_does_not_assert_fleet_state(self):
        self.assertEqual(gv.assess("short", fake_check), ("fail", False))

    def test_pass(self):
        self.assertEqual(gv.assess("a clean report about web things", fake_check), ("pass", False))


class FakeCur:
    def __init__(self, conn, rows=()):
        self.conn, self.rows = conn, list(rows)
        self.itersize = 0

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self): return iter(self.rows)

    def execute(self, sql, *a):
        self.conn.sqls.append(sql)

    def executemany(self, sql, params):
        self.conn.updates.extend(params)

    def copy_expert(self, sql, fh):
        self.conn.sqls.append(sql)
        fh.write(self.conn.dump_text)


class FakeConn:
    def __init__(self, rows=(), dump_text="id,filepath\n1,/a\n"):
        self.rows, self.sqls, self.updates = rows, [], []
        self.commits = self.rollbacks = 0
        self.dump_text = dump_text
        self.cursor_kwargs = []

    def cursor(self, **kw):
        self.cursor_kwargs.append(kw)
        return FakeCur(self, self.rows if kw.get("name") else ())

    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1


ROWS = [(1, "/s/a.md", "a clean report about web things"),
        (2, "/s/b.md", "see /opt/lumen/x " * 5),
        (3, "/s/c.md", "tiny")]


class BackfillTests(unittest.TestCase):
    def test_writes_both_columns_and_counts(self):
        conn = FakeConn(ROWS)
        c = gv.backfill(conn, fake_check)
        self.assertEqual(conn.updates, [("pass", False, 1), ("fail", True, 2), ("fail", False, 3)])
        self.assertEqual((c["seen"], c["pass"], c["fail"], c["asserts_fleet_state"]), (3, 1, 2, 1))
        self.assertTrue(conn.cursor_kwargs[0].get("withhold"))

    def test_dry_run_writes_nothing(self):
        conn = FakeConn(ROWS)
        c = gv.backfill(conn, fake_check, dry_run=True)
        self.assertEqual(conn.updates, [])
        self.assertEqual(c["seen"], 3)
        self.assertEqual(conn.rollbacks, 1)

    def test_null_only_unless_forced(self):
        conn = FakeConn(ROWS)
        gv.backfill(conn, fake_check)
        self.assertIn("WHERE grounding_verdict IS NULL", conn.sqls[0])
        conn = FakeConn(ROWS)
        gv.backfill(conn, fake_check, force=True)
        self.assertNotIn("IS NULL", conn.sqls[0])

    def test_batches_commit_as_they_go(self):
        conn = FakeConn(ROWS)
        gv.backfill(conn, fake_check, batch=2)
        self.assertEqual(conn.commits, 2)
        self.assertEqual(len(conn.updates), 3)

    def test_check_error_is_counted_not_fatal(self):
        def boom(text, **kw):
            raise RuntimeError("x")
        conn = FakeConn(ROWS)
        c = gv.backfill(conn, boom)
        self.assertEqual((c["errors"], conn.updates), (3, []))


class MigrateTests(unittest.TestCase):
    def test_refuses_without_dump(self):
        with self.assertRaises(SystemExit):
            gv.migrate(FakeConn(), None)

    def test_refuses_on_empty_dump_and_does_not_alter(self):
        conn = FakeConn(dump_text="")
        with tempfile.TemporaryDirectory() as d, self.assertRaises(SystemExit):
            gv.migrate(conn, os.path.join(d, "dump.csv"))
        self.assertFalse(any("ALTER TABLE" in s for s in conn.sqls))

    def test_dump_then_ddl(self):
        conn = FakeConn()
        with tempfile.TemporaryDirectory() as d:
            gv.migrate(conn, os.path.join(d, "dump.csv"))
        self.assertIn("COPY", conn.sqls[0])
        self.assertIn("ADD COLUMN IF NOT EXISTS grounding_verdict", conn.sqls[1])
        self.assertIn("ADD COLUMN IF NOT EXISTS asserts_fleet_state", conn.sqls[1])
        self.assertEqual(conn.commits, 1)

    def test_ddl_is_additive_only(self):
        for bad in ("DROP", "DELETE", "TRUNCATE", "UPDATE"):
            self.assertNotIn(bad, gv.MIGRATION_SQL.upper())


INDEXER_FIXTURE = '''\
from asserted_at import asserted_at as doc_asserted_at   # wq#1773

def upsert_batch(conn, rows):
    sql = """
    INSERT INTO research_docs (filepath, filename, category, mtime, size_bytes, content, content_hash, embedding, asserted_at)
    VALUES %s
    ON CONFLICT (filepath) DO UPDATE SET
      embedding=EXCLUDED.embedding, indexed_at=now(), asserted_at=EXCLUDED.asserted_at;
    """
    execute_values(cur, sql, rows, template="(%s,%s,%s,%s,%s,%s,%s,%s,%s)")

def main():
    batch.append((rel, fp.name, classify(fp.name), mtime, st.st_size, content, h, emb,
                          doc_asserted_at(content, mtime)))
'''


class PatcherTests(unittest.TestCase):
    def test_patch_applies_parses_and_is_idempotent(self):
        out, changed = pir.patch(INDEXER_FIXTURE)
        self.assertTrue(changed)
        ast.parse(out)
        self.assertIn("grounding_verdict, asserts_fleet_state)", out)
        self.assertIn("*doc_grounding(content)", out)
        self.assertEqual(out.count("%s,"), INDEXER_FIXTURE.count("%s,") + 2)
        again, changed2 = pir.patch(out)
        self.assertFalse(changed2)
        self.assertEqual(again, out)

    def test_column_and_placeholder_counts_agree(self):
        out, _ = pir.patch(INDEXER_FIXTURE)
        cols = out.split("INSERT INTO research_docs (")[1].split(")")[0].split(",")
        tmpl = out.split('template="(')[1].split(')"')[0].split(",")
        self.assertEqual(len(cols), len(tmpl))
        self.assertEqual(len(cols), 11)

    def test_drifted_file_aborts_untouched(self):
        with self.assertRaises(SystemExit):
            pir.patch(INDEXER_FIXTURE.replace("asserted_at)\n    VALUES", "asserted_at, x)\n    VALUES"))


class InstallVerifyTests(unittest.TestCase):
    """wq#2554: install.sh --verify must catch a tree that has the indexer patch
    (README step 2) but not the migration (step 1) — the state that crashed the
    2026-09-25 daily index run on UndefinedColumn."""

    HERE = os.path.dirname(os.path.abspath(__file__))
    INSTALL = os.path.join(HERE, "install.sh")

    def _root(self, *, db_has_columns=True, patched=True, copied=True):
        root = tempfile.mkdtemp()
        bin_d = os.path.join(root, "bin")
        retrieval = os.path.join(root, "minerva", "retrieval")
        os.makedirs(bin_d)
        os.makedirs(retrieval)
        src = os.path.join(self.HERE, "grounding_verdict.py")
        with open(src) as f:
            tool = f.read()
        for dst in [os.path.join(bin_d, "research-grounding-verdict")] + (
                [os.path.join(retrieval, "grounding_verdict.py")] if copied else []):
            with open(dst, "w") as f:
                f.write(tool)
        indexer = "import os\n"
        if patched:
            indexer += ("from grounding_verdict import parse_header as doc_grounding\n"
                        "SQL = 'grounding_verdict=EXCLUDED.grounding_verdict'\n")
        with open(os.path.join(retrieval, "index_research.py"), "w") as f:
            f.write(indexer)
        query = os.path.join(bin_d, "minerva-query")
        rows = "(1 rows)" if db_has_columns else "(0 rows)"
        with open(query, "w") as f:
            f.write("#!/bin/sh\necho '?column?'\necho '%s'\n" % rows)
        os.chmod(query, 0o755)
        return root

    def _verify(self, root):
        env = dict(os.environ,
                   BIN=os.path.join(root, "bin"),
                   MINERVA=os.path.join(root, "minerva"),
                   QUERY=os.path.join(root, "bin", "minerva-query"))
        return subprocess.run(["bash", self.INSTALL, "--verify"],
                              env=env, capture_output=True, text=True)

    def test_fully_deployed_tree_passes(self):
        r = self._verify(self._root())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("VERIFY OK", r.stdout)

    def test_missing_migration_fails_even_though_files_match(self):
        r = self._verify(self._root(db_has_columns=False))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("MISS db column absent: research_docs.grounding_verdict", r.stdout)
        self.assertIn("MISS db column absent: research_docs.asserts_fleet_state", r.stdout)

    def test_unpatched_indexer_and_uncopied_module_are_reported(self):
        r = self._verify(self._root(patched=False, copied=False))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("MISS absent:", r.stdout)
        self.assertIn("parse_header", r.stdout)

    def test_verify_installs_nothing(self):
        root = self._root()
        stamp = os.path.join(root, "bin", "research-grounding-verdict")
        before = os.stat(stamp).st_mtime_ns
        self._verify(root)
        self.assertEqual(os.stat(stamp).st_mtime_ns, before)

    def test_unreachable_db_exits_two(self):
        root = self._root()
        query = os.path.join(root, "bin", "minerva-query")
        with open(query, "w") as f:
            f.write("#!/bin/sh\nexit 3\n")
        os.chmod(query, 0o755)
        r = self._verify(root)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("VERIFY ERROR", r.stdout)


if __name__ == "__main__":
    unittest.main()
