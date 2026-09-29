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
# A pilot that already ran to completion (its own REPORT.md), whose Treatment comments
# discuss the LiteLLM `/chat/completions` endpoint. wq#3121 reproduced wq#3118's
# false-positive on exactly this card.
COMPLETED_CARD = os.path.join(REPO, 'pilots', '1a025ddf9834', 'CARD.md')

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


def test_parse_draft_reads_a_fenced_reply():
    assert psa.parse_draft('here you go\n```json\n' + GOOD_DRAFT + '\n```\nhope that helps'
                           )['hypothesis'].startswith('deepseek-v4-flash matches')
    assert psa.parse_draft('no json here') == {}


# wq#3118: seven pilot cards passed `validate()`'s command-SHAPE check yet named
# Baseline/Treatment paths nothing had ever created (agentbrew_sample.csv,
# iris-mini-pilot/qa_set.jsonl, dgm_v2/paired_eval/*, ...), so every one blocked on
# "baseline does not reproduce" under the wq#1559 card protocol. `lint_card` is the
# second gate the ticket asks for, run once the harness is supposed to exist.
def test_lint_card_passes_a_real_runner_authored_card():
    with open(RUNNER_FILLED_CARD) as fh:
        card = fh.read()
    assert psa.lint_card(card, REPO) == []


def test_lint_card_flags_a_phantom_baseline_path():
    card = psa.apply_spec(
        open(TEMPLATE).read(),
        psa.SpecResult(True, {**GOOD_SPEC, 'baseline':
                       'python3 pilots/wq3118-phantom/run_pilot.py --arm baseline'}))
    problems = psa.lint_card(card, REPO)
    assert any('pilots/wq3118-phantom/run_pilot.py' in p for p in problems), problems


def test_lint_card_skips_remote_ssh_lines():
    """A path named on an `ssh host '...'` line lives on the remote host and cannot be
    resolved from here — flagging it would conflate the wq#3118 missing-file defect
    with the separate, already-logged dispatch-grant gap (ISSUE-LOG row 1034)."""
    card = psa.apply_spec(
        open(TEMPLATE).read(),
        psa.SpecResult(True, {**GOOD_SPEC,
                       'baseline': "ssh hathor 'python3 /data/lumen/does/not/exist.py'",
                       'treatment': 'python3 pilots/9d95766f5f43/run_pilot.py --arm treatment'}))
    assert psa.lint_card(card, REPO) == []


# wq#3123 (from wq#3121's verify pass): _PATH_TOKEN read any `/`-led token as a local
# file, so an API route in a comment and a URL's path component both linted as missing.
# None of wq#3118's three probes had a URL or a comment line in them, so it shipped
# 14/14 green.
def test_lint_card_ignores_api_routes_in_comments():
    """Outside-the-design-population probe: a committed card from a pilot that already
    COMPLETED, so by construction nothing about it is unrunnable. Its Treatment block
    comments name LiteLLM's `/chat/completions` endpoint; wq#3118 flagged that as a
    path that 'does not resolve'."""
    with open(COMPLETED_CARD) as fh:
        card = fh.read()
    assert psa.lint_card(card, REPO) == []


def test_referenced_paths_ignores_a_urls_path_component():
    """`//` let the token regex re-anchor past the scheme, inventing the local path
    `/example.com/pilots/data.csv` out of a perfectly ordinary download."""
    assert psa._referenced_paths('curl https://example.com/pilots/data.csv') == []


def test_referenced_paths_still_reads_a_real_path_beside_a_url():
    """The URL is dropped, not the whole line — a phantom output path on a `curl` line
    is still the wq#3118 defect and must still be caught."""
    line = 'curl -o pilots/wq3123-phantom/out.json https://example.com/data.csv'
    assert psa._referenced_paths(line) == ['pilots/wq3123-phantom/out.json']


# wq#3129 (from wq#3126's verify pass): wq#3123's comment rule was anchored at line
# start, so only a whole-line comment was skipped. A trailing comment — the shape
# `pilots/0eed2f5bdb92/CARD.md:81-82` ships — still had its prose read for paths.
# wq#3123's three probes all used whole-line comments, so it shipped 17/17 green.
def test_lint_card_ignores_a_route_in_a_trailing_comment():
    """wq#3126's verbatim repro, through the public gate: a Baseline whose command
    carries a trailing comment naming an API route linted as 'does not resolve'."""
    card = psa.apply_spec(
        open(TEMPLATE).read(),
        psa.SpecResult(True, {**GOOD_SPEC,
                       'baseline': 'python3 pilots/9d95766f5f43/run_pilot.py  '
                                   '# see /api/v1/users for the schema',
                       'treatment': 'python3 pilots/9d95766f5f43/run_pilot.py --arm t'}))
    assert psa.lint_card(card, REPO) == []


def test_referenced_paths_still_reads_the_command_beside_a_trailing_comment():
    """The comment is dropped, not the whole line — a phantom path in the command part
    of a commented line is still the wq#3118 defect and must still be caught."""
    line = 'python3 pilots/wq3129-phantom/run.py  # writes /api/v1/users fixtures'
    assert psa._referenced_paths(line) == ['pilots/wq3129-phantom/run.py']


def test_referenced_paths_does_not_read_a_word_internal_hash_as_a_comment():
    """A `#` that does not start a word is part of the token, not a comment marker:
    ticket refs (`wq#3129`) and URL fragments both appear in this corpus."""
    assert psa._referenced_paths(
        'python3 pilots/wq3129-phantom/run.py --ticket wq#3129') == [
            'pilots/wq3129-phantom/run.py']
    assert psa._referenced_paths('curl https://example.com/page#pilots/data.csv') == []


def test_referenced_paths_still_skips_a_whole_line_comment():
    """wq#3123's own case, kept live: a `$ `-prefixed comment line names a route."""
    assert psa._referenced_paths('$ # the judge posts to /chat/completions') == []


def test_real_runner_authored_spec_passes_the_validator():
    """Outside-the-design-population probe: a committed card a runner wrote by hand,
    cited in wq#2690 as a legitimate fill-in. The validator must accept it."""
    with open(RUNNER_FILLED_CARD) as fh:
        card = fh.read()
    assert psa.unfilled_sections(card) == []
    sections = {key: section_text(card, heading)
                for heading, key in psa.HEADINGS.items()}
    assert psa.validate(sections) == []


# wq#2728: two of validate()'s per-section checks tested for keyword/digit PRESENCE
# rather than the keyword being load-bearing, so a draft that contradicts its own
# claim shipped as a clean card (no banner). Both strings are the ticket's verbatim
# repros and are committed here as the fixtures the structural checks are pinned to.
WQ2728_FIXED_SAMPLE = ('roughly 50 notes pulled fresh from the live queue each run count '
                       'varies day to day')
WQ2728_PRIMARY_METRIC = '5 things are listed below in the report for review'


def test_validator_rejects_keyword_only_match_on_fixed_sample():
    problems = psa.validate({**psa.parse_draft(GOOD_DRAFT),
                             'fixed_sample': WQ2728_FIXED_SAMPLE})
    assert problems, 'a count with no selection rule is not a pin'
    assert any(p.startswith('fixed_sample: not pinned') for p in problems), problems
    # the positive control: a count IS a pin once the rule that picks the rows is there
    assert not [p for p in psa.validate({**psa.parse_draft(GOOD_DRAFT), 'fixed_sample':
                'first 50 notes by id from the judge queue table, ordered by id'})
                if p.startswith('fixed_sample')]


def test_validator_rejects_keyword_only_match_on_primary_metric():
    problems = psa.validate({**psa.parse_draft(GOOD_DRAFT),
                             'primary_metric': WQ2728_PRIMARY_METRIC})
    assert problems, 'a digit and a comparator that are not one claim is not a win condition'
    assert any(p.startswith('primary_metric') for p in problems), problems
    # the shapes real cards use must still pass: a bare ratio, a signed relative gain,
    # a percentage in front of its comparator, a threshold phrase
    for metric in ('ROC-AUC, unitless. Win: treatment AUC >= 0.65 and >= baseline + 0.05',
                   'conversion rate on the pinned runs, win >= +10% relative',
                   'tokens per task; a win is the treatment using 20% fewer tokens',
                   'failures per 100 runs, threshold of 3 counts as a win'):
        assert not [p for p in psa.validate({**psa.parse_draft(GOOD_DRAFT),
                                             'primary_metric': metric})
                    if p.startswith('primary_metric')], metric
