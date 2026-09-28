"""Tests for ir_triage.py (wq#2836). Zero-quota: the model is a stub, every path is tmp."""
import json, sqlite3, sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import ir_triage as it

PROPOSALS_DDL = """CREATE TABLE proposals (id TEXT PRIMARY KEY, parent_id TEXT, title TEXT,
  source_intel_id TEXT, current_state TEXT, proposed_change TEXT, effort TEXT, impact TEXT,
  risk TEXT, rollback TEXT, status TEXT DEFAULT 'pending', eval_result TEXT,
  eval_passed INTEGER DEFAULT 0, created_at TEXT, decided_at TEXT, deployed_at TEXT,
  decision_reason TEXT, wq_ticket INTEGER)"""

AQ_DDL = """CREATE TABLE alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, first_ts TEXT NOT NULL,
  last_ts TEXT NOT NULL, source TEXT NOT NULL, key TEXT NOT NULL, title TEXT, message TEXT,
  priority TEXT DEFAULT 'default', count INTEGER DEFAULT 1, status TEXT DEFAULT 'open',
  owner TEXT DEFAULT 'unassigned', note TEXT, resolved_ts TEXT)"""

# Verbatim first line of the live 2026-09-27 report (trivy_20260927_090312.json).
TRIVY_FATAL = ('2026-09-27T09:13:43Z\tFATAL\tFatal error\trun error: rootfs scan error: scan error: '
               'scan failed: failed analysis: analyze with traversal: walk dir error\n')


def add_alert(db, source, key, title, priority='default', status='open', message=''):
    c = sqlite3.connect(db)
    c.execute('INSERT INTO alerts(first_ts,last_ts,source,key,title,message,priority,status) '
              'VALUES(?,?,?,?,?,?,?,?)', ('2026-09-28T01:00:00Z', '2026-09-28T03:00:00Z',
                                          source, key, title, message, priority, status))
    c.commit()
    c.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    aq = tmp_path / 'alerts.db'
    c = sqlite3.connect(aq); c.execute(AQ_DDL); c.commit(); c.close()
    mdb = tmp_path / 'minerva.db'
    c = sqlite3.connect(mdb); c.execute(PROPOSALS_DDL); c.commit(); c.close()
    (tmp_path / 'trivy').mkdir()
    (tmp_path / 'failed.json').write_text(json.dumps({'failed': ['lumen-backup-important.service'],
                                                      'checked_at': 1790390000}))
    monkeypatch.setattr(it, 'AQ_DB', aq)
    monkeypatch.setattr(it, 'MINERVA_DB', mdb)
    monkeypatch.setattr(it, 'FAILED_UNITS', tmp_path / 'failed.json')
    monkeypatch.setattr(it, 'TRIVY_DIR', tmp_path / 'trivy')
    monkeypatch.setattr(it, 'PROPOSALS_DIR', tmp_path / 'pending')
    monkeypatch.setattr(it, 'STATE', tmp_path / 'state.json')
    monkeypatch.setattr(it, 'LOG', tmp_path / 'ir.log')
    return tmp_path


class StubLLM:
    """Answers the three roles in order, citing whatever IDs the test chooses."""

    def __init__(self, evidence, cites, steps):
        self.evidence, self.cites, self.steps, self.calls = evidence, cites, steps, []

    def __call__(self, prompt):
        self.calls.append(prompt)
        if 'EVIDENCE-COLLECTION' in prompt:
            return json.dumps({'evidence': [{'id': i, 'why': 'x'} for i in self.evidence]})
        if 'CORRELATION' in prompt:
            return json.dumps({'hypothesis': 'Backups stopped running.', 'cites': self.cites,
                               'severity': 'high', 'confidence': 'medium', 'alternatives': ['timer drift']})
        return json.dumps({'title': 'backup timer dead', 'steps': self.steps, 'rollback': 'none needed'})


def test_collect_aq_keeps_high_and_security_rows_read_only(env):
    add_alert(env / 'alerts.db', 'spof-watch', 'timer:x', 'Dead-man: x', priority='urgent')
    add_alert(env / 'alerts.db', 'ntfy:lumen-alerts', 'k', 'probe DOWN', priority='4')
    add_alert(env / 'alerts.db', 'guard-fired', 'g', 'tripwire', priority='default')
    add_alert(env / 'alerts.db', 'jay-watch', 'n', 'noise', priority='default')
    add_alert(env / 'alerts.db', 'spof-watch', 'old', 'fixed', priority='urgent', status='resolved')
    before = (env / 'alerts.db').read_bytes()
    ids = [f['id'] for f in it.collect_aq()]
    assert ids == ['aq#1', 'aq#2', 'aq#3']
    assert (env / 'alerts.db').read_bytes() == before


def test_fragment_text_is_masked(env):
    secret = 'gh' + 'p_' + 'Q' * 36
    add_alert(env / 'alerts.db', 'guard-fired', 'g', 'tripwire', message=f'leaked {secret} in log')
    text = it.collect_aq()[0]['text']
    assert secret not in text
    assert 'leaked' in text  # masked by harness/masking.py, not withheld by the fallback


def test_trivy_fatal_report_is_a_fragment(env):
    (env / 'trivy' / 'trivy_20260927_090312.json').write_text(TRIVY_FATAL)
    [f] = it.collect_trivy()
    assert f['id'] == 'trivy:20260927_090312'
    assert f['ts'] == '2026-09-27T09:03:12Z'
    assert 'no parseable report' in f['text']


def test_trivy_counts_findings_and_clean_report_is_silent(env):
    vuln = {'VulnerabilityID': 'CVE-2026-0001', 'PkgName': 'openssl', 'Severity': 'CRITICAL',
            'InstalledVersion': '3.0', 'FixedVersion': '3.1'}
    (env / 'trivy' / 'trivy_20260920_090154.json').write_text(json.dumps({'Results': [{'Vulnerabilities': [vuln]}]}))
    assert '1 CRITICAL, 0 HIGH' in it.collect_trivy()[0]['text']
    (env / 'trivy' / 'trivy_20260927_090312.json').write_text(json.dumps({'Results': [{'Target': '/'}]}))
    assert it.collect_trivy() == []


def test_fabricated_citations_never_reach_a_proposal(env):
    add_alert(env / 'alerts.db', 'spof-watch', 'timer:b', 'Dead-man: backup', priority='urgent')
    llm = StubLLM(['aq#1'], ['aq#999'], [{'action': 'a', 'artifact': 'x', 'cites': ['aq#1']}])
    assert it.run(llm=llm) == 'no_incident'
    assert sqlite3.connect(env / 'minerva.db').execute('SELECT count(*) FROM proposals').fetchone()[0] == 0


def test_plan_steps_without_artifact_or_valid_cite_are_dropped(env):
    add_alert(env / 'alerts.db', 'spof-watch', 'timer:b', 'Dead-man: backup', priority='urgent')
    frags = it.collect()
    llm = StubLLM(['aq#1', 'unit:lumen-backup-important.service'], ['aq#1'], [
        {'action': 'check timer', 'artifact': 'lumen-backup-important.timer', 'cites': ['aq#1']},
        {'action': 'no artifact', 'artifact': '', 'cites': ['aq#1']},
        {'action': 'made-up cite', 'artifact': 'x', 'cites': ['aq#42']}])
    result, reason = it.triage(frags, llm)
    assert reason == 'ok'
    assert [s['action'] for s in result['steps']] == ['check timer']


def test_run_writes_one_blocked_proposal_then_dedups(env):
    add_alert(env / 'alerts.db', 'spof-watch', 'timer:b', 'Dead-man: backup', priority='urgent')
    llm = StubLLM(['aq#1', 'unit:lumen-backup-important.service'], ['aq#1', 'unit:lumen-backup-important.service'],
                  [{'action': 'inspect last run', 'artifact': 'lumen-backup-important.service', 'cites': ['aq#1']}])
    assert it.run(llm=llm) == 'proposed'
    row = sqlite3.connect(env / 'minerva.db').execute(
        'SELECT status, title, current_state, proposed_change, eval_result FROM proposals').fetchone()
    status, title, state, change, ev = row
    assert status == 'blocked'
    assert title == 'IR triage: backup timer dead'
    assert 'aq#1' in state and 'artifact: lumen-backup-important.service' in change
    ev = json.loads(ev)
    assert ev['explanation']['hypothesis_cites'] == ['aq#1', 'unit:lumen-backup-important.service']
    assert set(ev['explanation']['fragments']) == {'aq#1', 'unit:lumen-backup-important.service'}
    assert len(list((env / 'pending').glob('*.md'))) == 1

    n = len(llm.calls)
    assert it.run(llm=llm) == 'unchanged'
    assert len(llm.calls) == n

    add_alert(env / 'alerts.db', 'ntfy:lumen-alerts', 'k2', 'vault DOWN', priority='high')
    assert it.run(llm=llm) == 'proposed'
    assert sqlite3.connect(env / 'minerva.db').execute('SELECT count(*) FROM proposals').fetchone()[0] == 2


def test_model_failure_does_not_advance_seen_set(env):
    add_alert(env / 'alerts.db', 'spof-watch', 'timer:b', 'Dead-man: backup', priority='urgent')
    assert it.run(llm=lambda p: '') == 'llm_error'
    assert not (env / 'state.json').exists()


def test_long_plan_is_capped_to_whole_steps(env):
    step = {'action': 'z' * 300, 'artifact': 'lumen-x.service', 'cites': ['aq#1']}
    prop = it.to_proposal({'steps': [step] * it.MAX_STEPS, 'severity': 'high', 'confidence': 'low',
                           'hypothesis': 'h', 'cites': ['aq#1'], 'title': 't', 'rollback': ''})
    assert len(prop['proposed_change']) <= 2000
    assert prop['proposed_change'].endswith('[cites: aq#1]')


def test_explanation_stays_valid_json_under_cap(env):
    frags = {f'aq#{i}': {'id': f'aq#{i}', 'source': 'aq:x', 'ts': 't', 'text': 'y' * 400} for i in range(40)}
    result = {'cites': list(frags), 'steps': [], 'severity': 'high', 'confidence': 'low',
              'alternatives': [], 'fragments': frags}
    s = it.explanation(result)
    assert len(s) <= 4000
    json.loads(s)
