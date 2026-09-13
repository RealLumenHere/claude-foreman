#!/usr/bin/env python3
"""Tests for the wq#1614 contradiction penalty in search_v3.

Self-contained: builds a tiny archival corpus in a temp sqlite file and stubs the
embedding functions, so nothing here reads minerva.db. The identity test loads the
UNPATCHED mirror module alongside the patched one and compares their output on the
same corpus -- "1.0 is off" is demonstrated against the real previous code, not
asserted against a re-implementation of it.

Run:  python3 test_contra_penalty.py
"""
import importlib.util
import json
import os
import sqlite3
import struct
import sys
import tempfile
import unittest

MIRROR = '/home/lumen/research/minerva-ref/lumen_recall2.py'
PATCHED = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lumen_recall2.py')
FIXTURE = '/home/lumen/minerva/eval/fixtures/note_contradictions_20260913.json'

# 3-dim unit vectors: cosine against the query vector [1,0,0] is the first element.
NOTES = [
    ('n1aaaa', 'alpha note', 'procedure for the widget', (1.00, 0.000, 0.0)),
    ('n2bbbb', 'beta note', 'procedure for the widget', (0.90, 0.436, 0.0)),
    ('n3cccc', 'gamma note', 'procedure for the widget', (0.80, 0.600, 0.0)),
]


def _blob(vec):
    return struct.pack(f'<{len(vec)}f', *vec)


class _FakeMM:
    """Minimal stand-in for minerva_memory: deterministic, no model, no network."""

    @staticmethod
    def embed(_query):
        import numpy as np
        return np.array([1.0, 0.0, 0.0], dtype='float32')

    @staticmethod
    def decode_embedding(blob, expect_dim=None):
        import numpy as np
        if not blob:
            return None
        v = np.frombuffer(blob, dtype='float32')
        return v if expect_dim is None or v.shape[0] == expect_dim else None

    @staticmethod
    def cosine(a, b):
        import numpy as np
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    @staticmethod
    def search(_query, top_k=10):
        return []


def build_corpus(path, contradicted=(), open_col='resolved_at', with_table=True):
    conn = sqlite3.connect(path)
    conn.executescript('''
        CREATE TABLE archival_notes (id TEXT PRIMARY KEY, kind TEXT, title TEXT,
            content TEXT, tags TEXT, created_at TEXT, trust REAL, embedding BLOB,
            revoked_at TEXT, superseded_by TEXT);
        CREATE TABLE note_merges (absorbed_id TEXT);
        CREATE TABLE memory_edges (src_type TEXT, src_id TEXT, dst_type TEXT, dst_id TEXT);
        CREATE TABLE state_facts (id TEXT, subject TEXT, predicate TEXT, value TEXT,
            valid_to TEXT);
    ''')
    for nid, title, content, vec in NOTES:
        conn.execute('INSERT INTO archival_notes (id, kind, title, content, tags, '
                     'created_at, trust, embedding) VALUES (?,?,?,?,?,?,?,?)',
                     (nid, 'note', title, content, '', '2026-01-01', 0.8, _blob(vec)))
    conn.execute("INSERT INTO state_facts VALUES ('f1','nephthys','ts_ip','1.2.3.4',NULL)")
    if with_table:
        conn.execute(f'CREATE TABLE note_contradictions (id INTEGER PRIMARY KEY, '
                     f'note_id TEXT, fact_id TEXT, dead_value TEXT, {open_col} TEXT)')
        for i, nid in enumerate(contradicted, 1):
            conn.execute('INSERT INTO note_contradictions (id, note_id, fact_id, '
                         'dead_value) VALUES (?,?,?,?)', (i, nid, f'fact{i}', 'dead'))
    conn.commit()
    conn.close()


def load_module(path, name, db, env):
    """Import `path` under `name` with `env` applied, then point it at `db`."""
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update({k: str(v) for k, v in env.items()})
    try:
        sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    mod.DB = db
    mod.mm = _FakeMM
    mod._SUBJ_CACHE.update(ts=0, re=None)
    mod._TAG_CACHE.update(ts=0, tags=None)
    mod._DF_CACHE.update(ts=0, df=None, n=0)
    return mod


def scores(mod, query='procedure for the widget'):
    return {h['id']: h['score'] for h in mod.search_v3(query, top_k=10)}


class ContraPenaltyTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, 'test.db')

    def tearDown(self):
        self.tmp.cleanup()

    # ── 1.0 is off ────────────────────────────────────────────────────────────
    def test_w_contra_1_matches_unpatched_mirror(self):
        build_corpus(self.db, contradicted=['n1aaaa', 'n2bbbb'])
        base = load_module(MIRROR, 'lr_mirror', self.db, {})
        new = load_module(PATCHED, 'lr_patched', self.db, {'MEMORY_V4_W_CONTRA': '1.0'})
        self.assertEqual(base.search_v3('procedure for the widget', top_k=10),
                         new.search_v3('procedure for the widget', top_k=10))

    def test_w_contra_1_emits_no_contra_key(self):
        build_corpus(self.db, contradicted=['n1aaaa'])
        new = load_module(PATCHED, 'lr_patched', self.db, {'MEMORY_V4_W_CONTRA': '1.0'})
        self.assertTrue(all('_contra' not in h
                            for h in new.search_v3('procedure for the widget')))

    # ── the penalty itself ────────────────────────────────────────────────────
    def test_penalty_halves_only_contradicted_scores(self):
        build_corpus(self.db, contradicted=['n1aaaa'])
        base = load_module(PATCHED, 'lr_off', self.db, {'MEMORY_V4_W_CONTRA': '1.0'})
        pen = load_module(PATCHED, 'lr_on', self.db, {'MEMORY_V4_W_CONTRA': '0.5'})
        b, p = scores(base), scores(pen)
        self.assertAlmostEqual(p['n1aaaa'], round(b['n1aaaa'] * 0.5, 4), places=4)
        self.assertEqual(p['n2bbbb'], b['n2bbbb'])
        self.assertEqual(p['n3cccc'], b['n3cccc'])

    def test_penalty_demotes_the_contradicted_top_hit(self):
        build_corpus(self.db, contradicted=['n1aaaa'])
        base = load_module(PATCHED, 'lr_off', self.db, {'MEMORY_V4_W_CONTRA': '1.0'})
        self.assertEqual(base.search_v3('procedure for the widget')[0]['id'], 'n1aaaa')
        pen = load_module(PATCHED, 'lr_on', self.db, {'MEMORY_V4_W_CONTRA': '0.5'})
        order = [h['id'] for h in pen.search_v3('procedure for the widget')]
        self.assertNotEqual(order[0], 'n1aaaa')
        self.assertEqual(order[0], 'n2bbbb')

    def test_penalty_is_soft_not_a_filter(self):
        """Every contradicted note is still returned, and their order among
        themselves is unchanged -- a contradicted note is a demotion, not a drop."""
        build_corpus(self.db, contradicted=[n[0] for n in NOTES])
        base = load_module(PATCHED, 'lr_off', self.db, {'MEMORY_V4_W_CONTRA': '1.0'})
        pen = load_module(PATCHED, 'lr_on', self.db, {'MEMORY_V4_W_CONTRA': '0.5'})
        b_order = [h['id'] for h in base.search_v3('procedure for the widget')]
        p_hits = pen.search_v3('procedure for the widget')
        self.assertEqual([h['id'] for h in p_hits], b_order)
        self.assertTrue(all(h['_contra'] == 0.5 for h in p_hits))
        self.assertTrue(all(h['score'] > 0 for h in p_hits))

    def test_w_rec_stays_zero(self):
        """The penalty is not the freshness prior returning by another name."""
        mod = load_module(PATCHED, 'lr_rec', self.db, {'MEMORY_V4_W_CONTRA': '0.5'})
        self.assertEqual(mod.W_REC, 0.0)

    # ── the contradiction lookup ──────────────────────────────────────────────
    def test_resolved_rows_do_not_penalize(self):
        build_corpus(self.db, contradicted=['n1aaaa'])
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE note_contradictions SET resolved_at='2026-09-13'")
        conn.commit()
        conn.close()
        base = load_module(PATCHED, 'lr_off', self.db, {'MEMORY_V4_W_CONTRA': '1.0'})
        pen = load_module(PATCHED, 'lr_on', self.db, {'MEMORY_V4_W_CONTRA': '0.5'})
        self.assertEqual(scores(pen), scores(base))

    def test_cleared_at_schema_is_accepted(self):
        """wq#1610 shipped the column as cleared_at; the live export calls it
        resolved_at. Both must resolve to the same penalty."""
        build_corpus(self.db, contradicted=['n1aaaa'], open_col='cleared_at')
        pen = load_module(PATCHED, 'lr_on', self.db, {'MEMORY_V4_W_CONTRA': '0.5'})
        hits = {h['id']: h for h in pen.search_v3('procedure for the widget')}
        self.assertEqual(hits['n1aaaa']['_contra'], 0.5)
        self.assertNotIn('_contra', hits['n2bbbb'])

    def test_missing_table_fails_open(self):
        build_corpus(self.db, with_table=False)
        base = load_module(PATCHED, 'lr_off', self.db, {'MEMORY_V4_W_CONTRA': '1.0'})
        pen = load_module(PATCHED, 'lr_on', self.db, {'MEMORY_V4_W_CONTRA': '0.5'})
        self.assertEqual(scores(pen), scores(base))

    def test_unknown_schema_fails_open(self):
        """A table with neither resolution column must not raise and must not
        penalize -- silently penalizing every row would be the worst outcome."""
        build_corpus(self.db, with_table=False)
        conn = sqlite3.connect(self.db)
        conn.execute('CREATE TABLE note_contradictions (id INTEGER, note_id TEXT)')
        conn.execute("INSERT INTO note_contradictions VALUES (1,'n1aaaa')")
        conn.commit()
        conn.close()
        base = load_module(PATCHED, 'lr_off', self.db, {'MEMORY_V4_W_CONTRA': '1.0'})
        pen = load_module(PATCHED, 'lr_on', self.db, {'MEMORY_V4_W_CONTRA': '0.5'})
        self.assertEqual(scores(pen), scores(base))

    # ── fixture mode (how the benchmark reads contradictions) ─────────────────
    def test_fixture_mode_overrides_the_table(self):
        build_corpus(self.db, contradicted=['n3cccc'])
        fx = os.path.join(self.tmp.name, 'fx.json')
        with open(fx, 'w') as fh:
            json.dump([{'note_id': 'n1aaaa', 'resolved_at': None},
                       {'note_id': 'n2bbbb', 'resolved_at': '2026-09-13'}], fh)
        pen = load_module(PATCHED, 'lr_fx', self.db,
                          {'MEMORY_V4_W_CONTRA': '0.5', 'MEMORY_V4_CONTRA_FIXTURE': fx})
        hits = {h['id']: h for h in pen.search_v3('procedure for the widget')}
        self.assertEqual(hits['n1aaaa']['_contra'], 0.5)   # open in fixture
        self.assertNotIn('_contra', hits['n2bbbb'])        # resolved in fixture
        self.assertNotIn('_contra', hits['n3cccc'])        # table ignored

    def test_real_fixture_parses_to_open_note_ids(self):
        if not os.path.exists(FIXTURE):
            self.skipTest(f'{FIXTURE} not present')
        build_corpus(self.db)
        pen = load_module(PATCHED, 'lr_fx2', self.db,
                          {'MEMORY_V4_W_CONTRA': '0.5',
                           'MEMORY_V4_CONTRA_FIXTURE': FIXTURE})
        with open(FIXTURE) as fh:
            rows = json.load(fh)
        expect = {r['note_id'] for r in rows if r.get('resolved_at') is None}
        self.assertEqual(pen._fixture_contradicted(), expect)
        self.assertEqual(len(expect), 17)                  # 20 rows, 17 notes


class GateShimTest(unittest.TestCase):
    """contra_penalty_eval.run_bench is the one moving part that cannot be
    exercised here (recall_bench needs minerva.db), so its mechanism -- inject the
    STAGED lumen_recall2 into sys.modules so recall_bench's own `import
    lumen_recall2` resolves to it -- is tested against a stand-in bench."""

    def test_shim_injects_the_staged_module_and_env(self):
        spec = importlib.util.spec_from_file_location(
            'cpe', os.path.join(os.path.dirname(PATCHED), 'contra_penalty_eval.py'))
        cpe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cpe)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.abspath(tmp)
            fake_bench = os.path.join(tmp, 'recall_bench.py')
            with open(fake_bench, 'w') as fh:
                fh.write(
                    'import json, os, sys, time\n'
                    'import lumen_recall2\n'
                    'label = sys.argv[sys.argv.index("--label") + 1]\n'
                    'json.dump({"summary": {"module": lumen_recall2.__file__,\n'
                    '  "w": lumen_recall2.W_CONTRA,\n'
                    '  "fixture": os.environ.get("MEMORY_V4_CONTRA_FIXTURE"),\n'
                    '  "golden": sys.argv[sys.argv.index("--golden") + 1]}},\n'
                    '  open(os.path.join(%r, "results_%%s_x.json" %% label), "w"))\n'
                    % tmp)
            cpe.BENCH = __import__('pathlib').Path(fake_bench)
            cpe.EVAL_DIR = __import__('pathlib').Path(tmp)
            art = cpe.run_bench(PATCHED, 'shimtest', 0.5, '/dev/null', '/tmp/fx.json')

        self.assertEqual(os.path.realpath(art['summary']['module']),
                         os.path.realpath(PATCHED))
        self.assertEqual(art['summary']['w'], 0.5)
        self.assertEqual(art['summary']['fixture'], '/tmp/fx.json')
        self.assertEqual(art['summary']['golden'], '/dev/null')


if __name__ == '__main__':
    unittest.main(verbosity=2)
