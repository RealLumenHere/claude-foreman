#!/usr/bin/env python3
"""wq#2460 — offline tests for context-authority validation in the verify path.

Runs with NO network, NO /home/lumen/minerva access and NO live services:
dotenv/requests are stubbed before import and the DB/log paths are redirected
to a temp dir. Run from this directory:

    python3 test_wq2460_context_authority.py
"""
import json, sqlite3, sys, tempfile, time, types, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

sys.modules.setdefault('dotenv', types.SimpleNamespace(load_dotenv=lambda *a, **k: False))


class _NoNet:
    def __call__(self, *a, **k):
        raise AssertionError('network call attempted in offline test')
    post = get = __call__


sys.modules.setdefault('requests', types.SimpleNamespace(post=_NoNet(), get=_NoNet()))


def _load(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mv = _load('minerva_verify', HERE / 'minerva_verify.py')

TMP = Path(tempfile.mkdtemp(prefix='wq2460_'))
mv.LOG = TMP / 'verify.log'


def _mkdb(rows):
    """Temp DB with the state_facts columns the check SELECTs (real schema has
    more; the query names its columns so this subset is sufficient)."""
    path = TMP / f'sf_{time.time_ns()}.db'
    c = sqlite3.connect(path)
    c.execute('CREATE TABLE state_facts (subject TEXT, predicate TEXT, '
              'value TEXT, note TEXT, valid_to TEXT)')
    c.executemany('INSERT INTO state_facts VALUES (?,?,?,?,?)', rows)
    c.commit(); c.close()
    return path


OPEN_FACT = ('litellm gateway', 'runs_version', 'litellm 1.55 on port 4000', '', None)
CLOSED_FACT = ('dashboard service', 'listens_on', 'port 8080 legacy dashboard', '',
               '2026-08-01T00:00:00+00:00')
NO_HITS = lambda p: []


class ClassifyPremise(unittest.TestCase):
    def test_open_fact_is_authoritative(self):
        rows = [OPEN_FACT, CLOSED_FACT]
        lbl = mv.classify_premise_authority(
            'The litellm gateway runs version 1.55 on port 4000.', rows, recall=NO_HITS)
        self.assertEqual(lbl, 'AUTHORITATIVE')

    def test_only_closed_fact_is_stale(self):
        lbl = mv.classify_premise_authority(
            'The legacy dashboard service listens on port 8080.', [CLOSED_FACT],
            recall=NO_HITS)
        self.assertEqual(lbl, 'STALE')

    def test_no_match_anywhere_is_unverified(self):
        lbl = mv.classify_premise_authority(
            'Quantum widgets are enabled on the mainframe.', [OPEN_FACT],
            recall=NO_HITS)
        self.assertEqual(lbl, 'UNVERIFIED')

    def test_fresh_episode_is_authoritative(self):
        hits = lambda p: [{'score': 0.9, 'ts': time.time() - 3600}]
        lbl = mv.classify_premise_authority('Quantum widgets exist.', [], recall=hits)
        self.assertEqual(lbl, 'AUTHORITATIVE')

    def test_episode_without_timestamp_is_authoritative(self):
        hits = lambda p: [{'score': 0.9, 'title': 'no ts key'}]
        lbl = mv.classify_premise_authority('Quantum widgets exist.', [], recall=hits)
        self.assertEqual(lbl, 'AUTHORITATIVE')

    def test_aged_out_episode_is_stale(self):
        hits = lambda p: [{'score': 0.9, 'ts': time.time() - 90 * 86400}]
        lbl = mv.classify_premise_authority('Quantum widgets exist.', [], recall=hits)
        self.assertEqual(lbl, 'STALE')

    def test_low_score_episode_is_unverified(self):
        hits = lambda p: [{'score': 0.2, 'ts': time.time()}]
        lbl = mv.classify_premise_authority('Quantum widgets exist.', [], recall=hits)
        self.assertEqual(lbl, 'UNVERIFIED')

    def test_recall_error_fails_open_to_unverified(self):
        def boom(p):
            raise RuntimeError('embed service down')
        lbl = mv.classify_premise_authority('Quantum widgets exist.', [], recall=boom)
        self.assertEqual(lbl, 'UNVERIFIED')

    def test_iso_hit_timestamp_parsed(self):
        hits = lambda p: [{'score': 0.9, 'created_at': '2020-01-01T00:00:00Z'}]
        lbl = mv.classify_premise_authority('Quantum widgets exist.', [], recall=hits)
        self.assertEqual(lbl, 'STALE')


class LabelsEntryPoint(unittest.TestCase):
    def setUp(self):
        self._db, self._recall = mv.DB, mv._episode_recall
        mv._episode_recall = NO_HITS

    def tearDown(self):
        mv.DB, mv._episode_recall = self._db, self._recall

    def test_disabled_returns_none(self):
        old = mv.AUTHORITY_ENABLED
        mv.AUTHORITY_ENABLED = False
        try:
            self.assertEqual(mv.context_authority_labels(['A claim here.']), (None, None))
        finally:
            mv.AUTHORITY_ENABLED = old

    def test_unreadable_db_fails_open(self):
        mv.DB = TMP / 'does_not_exist.db'
        labels, err = mv.context_authority_labels(['A claim here.'])
        self.assertIsNone(labels)
        self.assertTrue(err and err.startswith('authority_check:'))

    def test_labels_against_temp_db(self):
        mv.DB = _mkdb([OPEN_FACT])
        labels, err = mv.context_authority_labels(
            ['The litellm gateway runs version 1.55 on port 4000.',
             'Martian dust storms are worsening.'])
        self.assertIsNone(err)
        self.assertEqual(labels, ['AUTHORITATIVE', 'UNVERIFIED'])


class AuthorityBlock(unittest.TestCase):
    def test_empty_when_no_labels(self):
        self.assertEqual(mv._authority_block(None), '')
        self.assertEqual(mv._authority_block([]), '')

    def test_empty_when_all_authoritative(self):
        self.assertEqual(mv._authority_block(['AUTHORITATIVE', 'AUTHORITATIVE']), '')

    def test_names_flagged_claims(self):
        block = mv._authority_block(['AUTHORITATIVE', 'STALE', 'UNVERIFIED'])
        self.assertIn('claim 2: STALE', block)
        self.assertIn('claim 3: UNVERIFIED', block)
        self.assertNotIn('claim 1', block)
        self.assertIn('CONTEXT AUTHORITY CHECK', block)


class PromptThreading(unittest.TestCase):
    def test_note_reaches_judge_prompt(self):
        captured = {}

        class _Resp:
            status_code = 200
            def json(self):
                return {'choices': [{'message': {'content':
                        json.dumps({'labels': ['SUPPORTED'], 'rationale': 'r'})}}]}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured['prompt'] = json['messages'][0]['content']
            return _Resp()

        old_req, old_key = mv.requests, mv.LITELLM_KEY
        mv.requests = types.SimpleNamespace(post=fake_post)
        mv.LITELLM_KEY = 'test-key'
        try:
            labels, _, err = mv._judge_once(['One claim.'], 'ctx', 'm',
                                            authority_note='XAUTHMARKERX')
            self.assertIsNone(err)
            self.assertIn('XAUTHMARKERX', captured['prompt'])
            mv._judge_once(['One claim.'], 'ctx', 'm')
            self.assertNotIn('XAUTHMARKERX', captured['prompt'])
        finally:
            mv.requests, mv.LITELLM_KEY = old_req, old_key


class VerifyWiring(unittest.TestCase):
    """verify() end-to-end with judge/citation/authority monkeypatched."""

    def setUp(self):
        self.saved = {k: getattr(mv, k) for k in
                      ('judge_entailment_uq', 'semantic_citation_score',
                       'context_authority_labels', 'DB', 'AUTHORITY_CAP')}
        mv.DB = TMP / 'no_log.db'   # verification_log INSERT fails open
        self.notes = {}

        def fake_judge(claims, context, authority_note=''):
            self.notes['note'] = authority_note
            return 1.0, ['SUPPORTED'] * len(claims), 'r', None, [1.0] * len(claims)

        mv.judge_entailment_uq = fake_judge
        mv.semantic_citation_score = lambda claims, ctx: (1.0, [], None)

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(mv, k, v)

    def test_unverified_premise_caps_final_but_not_passed(self):
        mv.context_authority_labels = lambda claims: (
            ['AUTHORITATIVE', 'UNVERIFIED'] * ((len(claims) + 1) // 2), None)
        r = mv.verify('The gateway runs fine. The moon module is deployed today.',
                      'ctx', subject='t')
        self.assertTrue(r['passed'])
        self.assertTrue(r['authority_capped'])
        self.assertAlmostEqual(r['final_score'], mv.FINAL_THRESHOLD)
        self.assertIn('UNVERIFIED', r['premise_authority'])
        self.assertIn('claim', self.notes['note'])   # judge saw the block

    def test_all_authoritative_leaves_score_alone(self):
        mv.context_authority_labels = lambda claims: (['AUTHORITATIVE'] * len(claims), None)
        r = mv.verify('The gateway runs fine on port 4000 today.', 'ctx', subject='t')
        self.assertTrue(r['passed'])
        self.assertFalse(r['authority_capped'])
        self.assertEqual(r['final_score'], 1.0)
        self.assertEqual(self.notes['note'], '')

    def test_failed_open_check_changes_nothing(self):
        mv.context_authority_labels = lambda claims: (None, 'authority_check:OperationalError')
        r = mv.verify('The gateway runs fine on port 4000 today.', 'ctx', subject='t')
        self.assertTrue(r['passed'])
        self.assertFalse(r['authority_capped'])
        self.assertIsNone(r['premise_authority'])
        self.assertEqual(r['final_score'], 1.0)

    def test_sub_threshold_cap_gates(self):
        mv.AUTHORITY_CAP = 0.5
        mv.context_authority_labels = lambda claims: (['STALE'] * len(claims), None)
        r = mv.verify('The gateway runs fine on port 4000 today.', 'ctx', subject='t')
        self.assertFalse(r['passed'])
        self.assertTrue(r['authority_capped'])
        self.assertAlmostEqual(r['final_score'], 0.5)
        self.assertIn('authority_cap', r['reason'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
