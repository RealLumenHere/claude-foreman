"""wq#2690 — a pilot card ships with a real spec, or it ships loudly broken. Never blank.

The regression under test is four pilots (wq#1933, wq#1953, wq#2124, wq#2654) that
parked on the same gap: `build_card()` rendered the metadata and left Hypothesis /
Primary metric / Fixed sample / Baseline / Treatment as template comments, so the
runner either hand-filled a measurement plan it had to guess or blocked forever.

Two of these probes read committed artifacts rather than strings written for this
test, so they can actually fail: `pilots/EXPERIMENT_CARD.template.md` (the real
generator input, which must still be detectably unfilled) and
`pilots/9d95766f5f43/CARD.md` (a real runner-authored spec that the ticket cites as
legitimate — if the validator rejects it, the validator is wrong, not the card).
"""
import json
import os
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HARNESS_DIR)
sys.path.insert(0, HARNESS_DIR)
import pilot_spec_author as psa  # noqa: E402

TEMPLATE = os.path.join(REPO, 'pilots', 'EXPERIMENT_CARD.template.md')
RUNNER_FILLED_CARD = os.path.join(REPO, 'pilots', '9d95766f5f43', 'CARD.md')

THIN_PROPOSAL = {
    'title': 'try a smaller judge rung',
    'decision_reason': 'we do not know until we measure it',
    'current_state': '',
    'proposed_change': '',
}

GOOD_SPEC = {
    'hypothesis': (
        'deepseek-v4-flash matches the current judge rung on verdict agreement within '
        '3 percent while cutting median judge latency by at least 40 percent on the '
        'pinned note set.'),
    'primary_metric': (
        'Median judge latency in ms per note. Win, fixed before the run: treatment '
        'median <= 60 percent of baseline median with verdict agreement >= 97 percent.'),
    'fixed_sample': (
        '/home/lumen/foreman/pilots/fixtures/judge_notes.jsonl, 400 notes, sha256 '
        'recorded in REPORT.md. Both arms read this file.'),
    'baseline': 'python3 pilots/judge_bench.py --rung current --out baseline.json',
    'treatment': (
        'python3 pilots/judge_bench.py --rung deepseek-v4-flash --out treatment.json'),
}
GOOD_DRAFT = json.dumps(GOOD_SPEC)

# wq#2728 — the two drafts that validated clean while contradicting themselves. Each one
# carries the keyword the section's check looked for ("50 notes" for the pin, "below" and
# "5" for the win condition) and says in the same breath that the thing is not fixed.
# Committed verbatim from the ticket so the probe is re-runnable, not paraphrased.
KEYWORD_ONLY_SAMPLE = ('roughly 50 notes pulled fresh from the live queue each run '
                       'count varies day to day')
KEYWORD_ONLY_METRIC = '5 things are listed below in the report for review'

VAGUE_DRAFT = json.dumps({
    'hypothesis': 'the smaller rung seems promising and could improve judging overall',
    'primary_metric': 'judge quality, measured appropriately as the run goes along',
    'fixed_sample': 'some recent notes from the judge queue, whatever is around',
    'baseline': 'run the judge as usual and see how it appears to behave',
    'treatment': 'run the judge with the new rung and compare it to the old one',
})


def replies(*texts):
    """A gateway stub that hands back one canned reply per call, then repeats the last."""
    calls = []

    def call(prompt):
        calls.append(prompt)
        return texts[min(len(calls) - 1, len(texts) - 1)]

    call.calls = calls
    return call


def section_text(card, heading):
    lines = card.splitlines()
    start, end = psa._section_bounds(lines, heading)
    return '\n'.join(lines[start:end]).replace('```', '').strip()


@pytest.fixture
def template():
    with open(TEMPLATE) as fh:
        return fh.read()


def test_template_ships_unfilled(template):
    """The defect itself: the generator's own input has no spec in it."""
    assert sorted(psa.unfilled_sections(template)) == sorted(psa.SECTIONS)


def test_thin_proposal_still_yields_a_complete_card(template):
    card, result = psa.fill_card(template, THIN_PROPOSAL, replies(GOOD_DRAFT))
    assert result.ok and result.attempts == 1
    assert psa.unfilled_sections(card) == []
    assert 'SPEC AUTHORING FAILED' not in card
    assert '40 percent' in section_text(card, 'Hypothesis')
    assert 'judge_bench.py' in section_text(card, 'Baseline (measure FIRST)')


def test_vague_draft_is_rejected_then_re_asked(template):
    call = replies(VAGUE_DRAFT, GOOD_DRAFT)
    card, result = psa.fill_card(template, THIN_PROPOSAL, call)
    assert result.ok and result.attempts == 2
    assert 'Your previous draft was REJECTED' in call.calls[1]
    assert 'not falsifiable' in call.calls[1]
    assert psa.unfilled_sections(card) == []


def test_two_bad_drafts_ship_loud_and_never_blank(template):
    card, result = psa.fill_card(template, THIN_PROPOSAL, replies(VAGUE_DRAFT))
    assert not result.ok and result.attempts == psa.MAX_ATTEMPTS
    assert 'SPEC AUTHORING FAILED' in card and 'DO NOT RUN' in card
    assert 'REJECTED DRAFT' in card
    # Loud beats blank: the sections carry the rejected text, so nothing is empty.
    assert psa.unfilled_sections(card) == []


def test_gateway_down_never_blocks_card_creation(template):
    card, result = psa.fill_card(template, THIN_PROPOSAL, replies(None))
    assert not result.ok and result.problems == ['gateway unreachable']
    assert 'SPEC AUTHORING FAILED' in card
    assert 'NOT AUTHORED' in card
    assert psa.unfilled_sections(card) == []


def test_non_json_reply_is_rejected_not_pasted(template):
    card, result = psa.fill_card(
        template, THIN_PROPOSAL, replies('Sure! Here is my plan: run it and see.'))
    assert not result.ok
    assert 'not the requested JSON object' in result.banner_reason
    assert 'run it and see' not in card


def test_validator_flags_each_unfalsifiable_shape():
    assert any('no comparison' in p for p in psa.validate(
        {**psa.parse_draft(GOOD_DRAFT),
         'hypothesis': 'the new rung produces verdicts on the pinned note set of 400 notes'}))
    assert any('no numeric threshold' in p for p in psa.validate(
        {**psa.parse_draft(GOOD_DRAFT),
         'hypothesis': 'the new rung beats the current rung on verdict agreement'}))
    assert any('no win condition' in p for p in psa.validate(
        {**psa.parse_draft(GOOD_DRAFT),
         'primary_metric': 'median judge latency, reported in ms per note across 400 notes'}))
    assert any('not pinned' in p for p in psa.validate(
        {**psa.parse_draft(GOOD_DRAFT), 'fixed_sample': 'recent judge notes from the queue'}))
    assert any('no runnable command' in p for p in psa.validate(
        {**psa.parse_draft(GOOD_DRAFT), 'baseline': 'measure the judge the way we do today'}))


def test_validator_rejects_keyword_only_match_on_fixed_sample():
    """wq#2728: "50 notes" is not a pin when the same sentence says the count varies.
    The rest of the draft is the good one, so a non-empty result can only be the sample."""
    problems = psa.validate({**psa.parse_draft(GOOD_DRAFT),
                             'fixed_sample': KEYWORD_ONLY_SAMPLE})
    assert problems
    assert all(p.startswith('fixed_sample:') for p in problems), problems
    assert any('not pinned' in p for p in problems)


def test_validator_rejects_keyword_only_match_on_primary_metric():
    """wq#2728: `below` and `5` both appear and neither binds the other, so this names
    no win condition at all."""
    problems = psa.validate({**psa.parse_draft(GOOD_DRAFT),
                             'primary_metric': KEYWORD_ONLY_METRIC})
    assert problems
    assert all(p.startswith('primary_metric:') for p in problems), problems
    assert any('no win condition' in p for p in problems)


@pytest.mark.parametrize('key,text', [
    # A count pin with no path in sight — the weakest shape the prompt still accepts.
    ('fixed_sample', '400 notes in the judge queue export of 2026-09-01, one row per '
                     'note, both arms read the same 400'),
    ('primary_metric', 'Timeout rate as a percent of runs. Win, fixed before the run: '
                       'treatment rate below 2 percent.'),
    ('primary_metric', 'Median wall-clock seconds per note; win fixed before the run: '
                       'treatment is 25 percent faster than baseline.'),
    ('primary_metric', 'Judge latency in ms per note, threshold of 200 ms to win, '
                       'fixed before the run.'),
])
def test_validator_still_accepts_legitimate_binding_shapes(key, text):
    """The tightening in wq#2728 must reject unbound keywords, not bound ones stated in
    prose the GOOD_SPEC shape does not cover."""
    assert psa.validate({**psa.parse_draft(GOOD_DRAFT), key: text}) == []


def test_parse_draft_reads_a_fenced_reply():
    assert psa.parse_draft('here you go\n```json\n' + GOOD_DRAFT + '\n```\nhope that helps'
                           )['hypothesis'].startswith('deepseek-v4-flash matches')
    assert psa.parse_draft('no json here') == {}


def test_real_runner_authored_spec_passes_the_validator():
    """Outside-the-design-population probe: a committed card a runner wrote by hand,
    cited in wq#2690 as a legitimate fill-in. The validator must accept it."""
    with open(RUNNER_FILLED_CARD) as fh:
        card = fh.read()
    assert psa.unfilled_sections(card) == []
    sections = {key: section_text(card, heading)
                for heading, key in psa.HEADINGS.items()}
    assert psa.validate(sections) == []
