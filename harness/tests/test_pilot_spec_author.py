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

VAGUE_DRAFT = json.dumps({
    'hypothesis': 'the smaller rung seems promising and could improve judging overall',
    'primary_metric': 'judge quality, measured appropriately as the run goes along',
    'fixed_sample': 'some recent notes from the judge queue, whatever is around',
    'baseline': 'run the judge as usual and see how it appears to behave',
    'treatment': 'run the judge with the new rung and compare it to the old one',
})


# wq#2728, verbatim from the escalation: two drafts that used to validate clean
# because a keyword and a digit were merely PRESENT. Each contradicts its own section.
KEYWORD_ONLY_SAMPLE = ('roughly 50 notes pulled fresh from the live queue each run '
                       'count varies day to day')
KEYWORD_ONLY_METRIC = '5 things are listed below in the report for review'


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
    """wq#2728 repro 1: the old rule read the bare `50 notes` token as a pin, in a
    sentence that says outright the count is different every run."""
    sections = {**psa.parse_draft(GOOD_DRAFT), 'fixed_sample': KEYWORD_ONLY_SAMPLE}
    problems = psa.validate(sections)
    assert problems
    assert any('fixed_sample' in p for p in problems)


def test_validator_rejects_keyword_only_match_on_primary_metric():
    """wq#2728 repro 2: `below` and a stray `5` used to add up to a win condition in
    a sentence that names no metric, no unit and nothing to beat."""
    sections = {**psa.parse_draft(GOOD_DRAFT), 'primary_metric': KEYWORD_ONLY_METRIC}
    problems = psa.validate(sections)
    assert problems
    assert any('primary_metric' in p for p in problems)


def test_pinning_and_win_conditions_still_pass_in_their_real_shapes():
    """The other half of the gap: tightening must not start rejecting the shapes real
    cards use. Each of these must remain a clean draft."""
    for sample in ('/home/lumen/foreman/pilots/fixtures/judge_notes.jsonl, both arms',
                   '400 notes drawn once and committed; both arms read the same file',
                   'sha256 ' + 'a' * 64 + ' — the frozen extract both arms read'):
        assert psa.validate({**psa.parse_draft(GOOD_DRAFT), 'fixed_sample': sample}) == []
    for metric in ('verdict agreement in percent; win: treatment >= 97 percent',
                   'median latency in ms per note; win is at least 40 percent faster',
                   'ROC-AUC on the pinned set; win threshold 0.65, fixed before the run',
                   'accepted proposals per 100 draws; win: treatment mean beats '
                   'baseline mean by 2.0 accepted/100'):
        assert psa.validate({**psa.parse_draft(GOOD_DRAFT), 'primary_metric': metric}) == []


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
