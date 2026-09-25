"""wq#2573: backlog funnel + aging digest for lumen_reflector/lumen_dream
proposals (wq#2188 found 36 unapplied, oldest 87 days, no review funnel)."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backlog_digest as bd  # noqa: E402


def _write(dir_, name, generated, proposals):
    (dir_ / name).write_text(json.dumps({'generated': generated, 'proposals': proposals}))


def test_parse_generated_matches_lumen_reflector_stamp_format():
    ts = bd.parse_generated('2026-09-14 18:30 PT')
    assert ts == datetime(2026, 9, 14, 18, 30, tzinfo=bd.PT)


def test_parse_generated_rejects_malformed_stamp():
    assert bd.parse_generated('not a stamp') is None
    assert bd.parse_generated(None) is None


def test_load_reflections_skips_unparseable_json(tmp_path):
    (tmp_path / 'reflection-2026-09-14.json').write_text('{not json')
    _write(tmp_path, 'reflection-2026-09-21.json', '2026-09-21 18:30 PT',
           [{'type': 'lesson', 'title': 'x'}])
    out = bd.load_reflections(tmp_path)
    assert len(out) == 1
    assert len(out[0]['proposals']) == 1


def test_funnel_counts_per_week_and_honest_zero_without_ledger(tmp_path):
    _write(tmp_path, 'reflection-2026-09-14.json', '2026-09-14 18:30 PT',
           [{'title': 'a'}, {'title': 'b'}])
    _write(tmp_path, 'reflection-2026-09-21.json', '2026-09-21 18:30 PT',
           [{'title': 'c'}])
    reflections = bd.load_reflections(tmp_path)
    weeks = bd.funnel(reflections)
    assert weeks['2026-W38'] == {'created': 2, 'reviewed': 0, 'applied': 0}
    assert weeks['2026-W39'] == {'created': 1, 'reviewed': 0, 'applied': 0}


def test_funnel_counts_reviewed_applied_from_ledger(tmp_path):
    _write(tmp_path, 'reflection-2026-09-14.json', '2026-09-14 18:30 PT',
           [{'title': 'a'}, {'title': 'b'}])
    reflections = bd.load_reflections(tmp_path)
    weeks = bd.funnel(reflections, applied_titles={'a'})
    assert weeks['2026-W38'] == {'created': 2, 'reviewed': 1, 'applied': 1}


def test_aging_splits_stale_and_fresh(tmp_path):
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    _write(tmp_path, 'reflection-old.json', '2026-06-01 18:30 PT', [{'title': 'ancient'}])
    _write(tmp_path, 'reflection-new.json', '2026-09-24 18:30 PT', [{'title': 'fresh'}])
    reflections = bd.load_reflections(tmp_path)
    stale, fresh = bd.aging(reflections, age_days=14, now=now)
    assert [r['path'].name for r in stale] == ['reflection-old.json']
    assert [r['path'].name for r in fresh] == ['reflection-new.json']


def test_render_digest_lists_stale_proposals_and_never_claims_applied(tmp_path):
    _write(tmp_path, 'reflection-old.json', '2026-06-01 18:30 PT',
           [{'type': 'prune', 'title': 'stale note', 'target': 'sectionX'}])
    reflections = bd.load_reflections(tmp_path)
    stale, _ = bd.aging(reflections, age_days=14, now=datetime(2026, 9, 25, tzinfo=timezone.utc))
    text = bd.render_digest(stale, generated_at=datetime(2026, 9, 25, tzinfo=timezone.utc))
    assert 'Proposal-only' in text
    assert 'stale note' in text
    assert 'sectionX' in text
    assert '114d old' in text
