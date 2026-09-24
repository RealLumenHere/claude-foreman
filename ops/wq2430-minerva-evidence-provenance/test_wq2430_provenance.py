#!/usr/bin/env python3
"""wq#2430 — offline tests for the provenance-tagged evidence contract.

Runs with NO network, NO /home/lumen/minerva access and NO live services:
dotenv/requests are stubbed before import, the embedder is monkeypatched, and
the DB/log paths are redirected to a temp dir. Run from this directory:

    python3 test_wq2430_provenance.py
"""
import json, sys, tempfile, time, types, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Stub the two import-time deps so the staged modules load in any sandbox:
# dotenv (reads /home/lumen/agent/.env — unreadable here) and requests (must
# never fire for real in a test).
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


# Register the staged factpack under its real name so minerva_verify's
# `import minerva_factpack` handoff resolves to the copy under test.
fp = _load('minerva_factpack', HERE / 'minerva_factpack.py')
mv = _load('minerva_verify', HERE / 'minerva_verify.py')

NOW = time.time()
FRESH_ISO = datetime.now(timezone.utc).isoformat(timespec='seconds')
OLD_ISO = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec='seconds')


def chunk(text, ingested=FRESH_ISO, stale=False, ref='cmd://test'):
    return {'text': text, 'source': 'fact', 'source_ref': ref,
            'ingested_at': ingested, 'stale': stale}


class TagProvenance(unittest.TestCase):
    def test_schema_and_source_refs(self):
        out = fp.tag_provenance(['[service] lumen-x exists (running now): d',
                                 '[model] m is registered in the LiteLLM gateway model list',
                                 'untagged line here'], generated_at=NOW)
        self.assertEqual([c['source'] for c in out], ['service', 'model', 'unknown'])
        for c in out:
            self.assertEqual(sorted(c), ['ingested_at', 'source', 'source_ref', 'stale', 'text'])
            self.assertFalse(c['stale'])
        self.assertEqual(out[1]['source_ref'], 'file:///home/lumen/agent/litellm_config.yaml')
        self.assertEqual(out[2]['source_ref'], 'probe://unknown')

    def test_fresh_probe_not_stale_old_probe_stale(self):
        fresh, = fp.tag_provenance(['[fact] x'], generated_at=NOW)
        old, = fp.tag_provenance(['[fact] x'], generated_at=NOW - fp.EVIDENCE_TTL - 60)
        self.assertFalse(fresh['stale'])
        self.assertTrue(old['stale'])

    def test_capability_card_date_overrides_build_time(self):
        line = ('[capability] mod.py: does NOT implement graph layer '
                '(verified from source, 2026-01-01)')
        c, = fp.tag_provenance([line], generated_at=NOW)
        self.assertTrue(c['stale'])
        self.assertTrue(c['ingested_at'].startswith('2026-01-01'))

    def test_ttl_zero_disables_flag(self):
        old_ttl = fp.EVIDENCE_TTL
        try:
            fp.EVIDENCE_TTL = 0
            c, = fp.tag_provenance(['[fact] x'], generated_at=NOW - 10**9)
            self.assertFalse(c['stale'])
        finally:
            fp.EVIDENCE_TTL = old_ttl


class CorpusCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cache = fp.CACHE
        fp.CACHE = Path(self.tmp.name) / 'cache.json'

    def tearDown(self):
        fp.CACHE = self.old_cache
        self.tmp.cleanup()

    def test_v1_list_cache_still_readable(self):
        fp.CACHE.write_text(json.dumps(['[fact] legacy line one two']))
        self.assertEqual(fp.corpus(), ['[fact] legacy line one two'])
        self.assertIsNotNone(fp._CORPUS_META['generated_at'])

    def test_v2_dict_cache_roundtrip(self):
        fp.CACHE.write_text(json.dumps({'generated_at': NOW - 5, 'lines': ['[fact] a']}))
        self.assertEqual(fp.corpus(), ['[fact] a'])
        self.assertAlmostEqual(fp._CORPUS_META['generated_at'], NOW - 5, delta=1)


class RetrieveEvidence(unittest.TestCase):
    def test_wraps_retrieve(self):
        old = fp.retrieve
        try:
            fp.retrieve = lambda q, k=fp.TOP_K: ['[hw] CPU: xeon, all local inference is CPU-based']
            out = fp.retrieve_evidence('cpu question')
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]['source'], 'hw')
            self.assertIn('text', out[0])
        finally:
            fp.retrieve = old


class BuildContextHandoff(unittest.TestCase):
    def test_last_evidence_keyed_to_answer(self):
        old = fp.retrieve
        try:
            lines = [f'[fact] fact number {i} about the alpha subsystem' for i in range(6)]
            fp.retrieve = lambda q, k=fp.TOP_K: lines
            ctx = fp.build_context('the alpha subsystem answer')
            self.assertTrue(all(l in ctx for l in lines))
            self.assertEqual(fp.LAST_EVIDENCE['key'],
                             fp._evidence_key('the alpha subsystem answer'))
            self.assertEqual([c['text'] for c in fp.LAST_EVIDENCE['chunks']], lines)
        finally:
            fp.retrieve = old

    def test_fallback_clears_key(self):
        old = fp.retrieve
        try:
            fp.retrieve = lambda q, k=fp.TOP_K: []          # thin retrieval -> fallback
            fp.build_context('whatever answer text')
            self.assertIsNone(fp.LAST_EVIDENCE['key'])
            self.assertEqual(fp.LAST_EVIDENCE['chunks'], [])
        finally:
            fp.retrieve = old


def fake_embed(texts):
    """[1,0] for alpha-texts, [0,1] otherwise — cosine 1.0 within a group."""
    return [[1.0, 0.0] if 'alpha' in t else [0.0, 1.0] for t in texts]


class StaleSupport(unittest.TestCase):
    def setUp(self):
        self.old_embed = mv._embed_batch
        mv._embed_batch = fake_embed

    def tearDown(self):
        mv._embed_batch = self.old_embed

    def test_stale_only_support_flagged(self):
        flagged, err = mv.stale_support_claims(
            ['The alpha subsystem runs today.'],
            [chunk('[fact] alpha subsystem present', ingested=OLD_ISO, ref='cmd://old')])
        self.assertIsNone(err)
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]['stale_sources'], ['cmd://old'])

    def test_fresh_support_not_flagged(self):
        flagged, _ = mv.stale_support_claims(
            ['The alpha subsystem runs today.'],
            [chunk('[fact] alpha subsystem present', ingested=OLD_ISO),
             chunk('[fact] alpha subsystem confirmed', ingested=FRESH_ISO)])
        self.assertEqual(flagged, [])

    def test_unsupported_claim_not_flagged(self):
        flagged, _ = mv.stale_support_claims(
            ['The beta subsystem runs today.'],
            [chunk('[fact] alpha subsystem present', ingested=OLD_ISO)])
        self.assertEqual(flagged, [])

    def test_no_stale_chunks_short_circuits(self):
        flagged, err = mv.stale_support_claims(
            ['The alpha subsystem runs today.'],
            [chunk('[fact] alpha subsystem present', ingested=FRESH_ISO)])
        self.assertEqual((flagged, err), ([], None))

    def test_ttl_zero_disables_gate(self):
        old = mv.EVIDENCE_TTL_S
        try:
            mv.EVIDENCE_TTL_S = 0
            flagged, err = mv.stale_support_claims(
                ['The alpha subsystem runs today.'],
                [chunk('[fact] alpha subsystem present', ingested=OLD_ISO)])
            self.assertEqual((flagged, err), ([], None))
        finally:
            mv.EVIDENCE_TTL_S = old

    def test_stale_flag_honored_when_timestamp_missing(self):
        c = chunk('[fact] alpha subsystem present', stale=True)
        del c['ingested_at']
        flagged, _ = mv.stale_support_claims(['The alpha subsystem runs today.'], [c])
        self.assertEqual(len(flagged), 1)

    def test_keyword_fallback_when_embedder_down(self):
        mv._embed_batch = lambda texts: None
        flagged, err = mv.stale_support_claims(
            ['The alpha subsystem runs on lumen.'],
            [chunk('[fact] the alpha subsystem runs on lumen', ingested=OLD_ISO)])
        self.assertEqual(err, 'embed_unavailable')
        self.assertEqual(len(flagged), 1)

    def test_malformed_evidence_fails_open(self):
        flagged, err = mv.stale_support_claims(
            ['The alpha subsystem runs today.'], ['not-a-dict', {'no_text': 1}])
        self.assertEqual(flagged, [])
        self.assertIsNone(err)


class VerifyGate(unittest.TestCase):
    """verify() end-to-end with the judge, embedder, DB and log stubbed."""

    ANSWER = 'The alpha subsystem runs on Lumen today.'
    CONTEXT = 'Header line for the digest.\n[fact] alpha subsystem present on lumen\n'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (mv._embed_batch, mv.judge_entailment_uq, mv.log, mv.DB)
        mv._embed_batch = fake_embed
        mv.judge_entailment_uq = lambda claims, ctx: (
            1.0, ['SUPPORTED'] * len(claims), 'stub', None, [1.0] * len(claims))
        mv.log = lambda msg: None
        mv.DB = Path(self.tmp.name) / 'test.db'

    def tearDown(self):
        mv._embed_batch, mv.judge_entailment_uq, mv.log, mv.DB = self.saved
        self.tmp.cleanup()

    def test_passes_with_fresh_evidence(self):
        v = mv.verify(self.ANSWER, self.CONTEXT, subject='t', mode='semantic',
                      evidence=[chunk('[fact] alpha subsystem present', ingested=FRESH_ISO)])
        self.assertTrue(v['passed'])
        self.assertEqual(v['stale_support_claims'], [])
        self.assertEqual(v['evidence_chunks'], 1)

    def test_rejects_on_stale_only_evidence(self):
        v = mv.verify(self.ANSWER, self.CONTEXT, subject='t', mode='semantic',
                      evidence=[chunk('[fact] alpha subsystem present', ingested=OLD_ISO)])
        self.assertFalse(v['passed'])
        self.assertIn('stale_evidence(1)', v['reason'])
        self.assertEqual(len(v['stale_support_claims']), 1)

    def test_no_evidence_is_current_behavior(self):
        fp.LAST_EVIDENCE['key'], fp.LAST_EVIDENCE['chunks'] = None, []
        v = mv.verify(self.ANSWER, self.CONTEXT, subject='t', mode='semantic')
        self.assertTrue(v['passed'])
        self.assertEqual(v['evidence_chunks'], 0)

    def test_factpack_handoff_fires_gate_without_caller_change(self):
        fp.LAST_EVIDENCE['key'] = fp._evidence_key(self.ANSWER)
        fp.LAST_EVIDENCE['chunks'] = [chunk('[fact] alpha subsystem present', ingested=OLD_ISO)]
        try:
            v = mv.verify(self.ANSWER, self.CONTEXT, subject='t', mode='semantic')
            self.assertFalse(v['passed'])
            self.assertIn('stale_evidence', v['reason'])
        finally:
            fp.LAST_EVIDENCE['key'], fp.LAST_EVIDENCE['chunks'] = None, []

    def test_handoff_key_mismatch_skips_gate(self):
        fp.LAST_EVIDENCE['key'] = fp._evidence_key('some OTHER answer entirely')
        fp.LAST_EVIDENCE['chunks'] = [chunk('[fact] alpha subsystem present', ingested=OLD_ISO)]
        try:
            v = mv.verify(self.ANSWER, self.CONTEXT, subject='t', mode='semantic')
            self.assertTrue(v['passed'])
            self.assertEqual(v['evidence_chunks'], 0)
        finally:
            fp.LAST_EVIDENCE['key'], fp.LAST_EVIDENCE['chunks'] = None, []


if __name__ == '__main__':
    unittest.main(verbosity=2)
