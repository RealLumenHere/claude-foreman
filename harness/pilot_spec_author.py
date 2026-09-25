"""wq#2690 — the five spec sections of a pilot card get an owner: the generator.

A pilot card (`pilots/EXPERIMENT_CARD.template.md`) has two halves. The metadata
table and the two proposal-text sections are filled by `minerva_defer_review`'s
`build_card()`. The five sections that ARE the runnable pilot — Hypothesis,
Primary metric, Fixed sample, Baseline, Treatment — were left as template comments
with no owner, so four pilots (wq#1933, wq#1953, wq#2124, wq#2654) parked on the
same gap for weeks: runners either hand-filled the spec from the proposal (only
possible when their own worktree write happened to be permitted — wq#2654's first
attempt was killed for exactly that move) or correctly refused to guess a
measurement plan and blocked, after which nothing else ever filled it.

This module moves the authoring one step earlier, into card generation, using the
same LiteLLM gateway `minerva_defer_review` already calls for the wq#1559 enforced
re-run. One reviewable authoring step instead of N per-attempt interpretations, and
no runner write is needed at all.

FAIL LOUD, NEVER BLANK, NEVER BLOCK. A draft that is not falsifiable, or has no
measurable primary metric, or does not pin its sample, is rejected and re-asked once
with the reasons attached. If the second draft is also bad — or the gateway is down —
the card is still written, with a DO-NOT-RUN banner and the rejected draft quoted in
place. Rationale: refusing to create the card resurrects the failure mode this
ticket exists to kill (the pilot silently disappears and nothing closes the loop).
A loud broken card is visible, reviewable, and still un-runnable, because the
runner doctrine already forbids running a spec it would have had to guess.

Entry point for the generator, three lines inside `build_card()` after the template
render:

    sys.path.insert(0, '/home/lumen/foreman/harness')
    import pilot_spec_author
    tpl, spec = pilot_spec_author.fill_card(tpl, r, pilot_spec_author.gateway_call())

where `r` is the proposal row (title / decision_reason / current_state /
proposed_change) and `call(prompt) -> str | None` is one synchronous gateway call.

`validate()` gates a generator-authored draft, and is stricter than the historical
hand-filled corpus: swept over the 49 cards committed under `pilots/` as of this
commit, 22 are unfilled, 18 are flagged (23 of those flags are "no runnable command
line" — the older cards describe an arm in prose and keep the command in the run
script) and 9 pass clean. That strictness is the point here: the prompt below asks
for commands, and a command is exactly what a runner cannot guess.
"""
import json
import os
import re

SECTIONS = ('hypothesis', 'primary_metric', 'fixed_sample', 'baseline', 'treatment')

# Card heading -> spec key. Matched on the heading text as the template writes it.
HEADINGS = {
    'Hypothesis': 'hypothesis',
    'Primary metric': 'primary_metric',
    'Fixed sample': 'fixed_sample',
    'Baseline (measure FIRST)': 'baseline',
    'Treatment': 'treatment',
}

MAX_ATTEMPTS = 2

# Words that turn a hypothesis into a press release. Kept to the ones that signal no
# commitment at all: "improves by 40%" is fine and the numeric rules below police it,
# so hedges like "significantly" are not listed. "explore-exploit" is a real technique,
# hence the lookahead — a real card (pilots/9d95766f5f43) was rejected without it.
VAGUE = re.compile(
    r'\b(promising|promise|better overall|seems?|maybe|might|could|'
    r'explore(?!-)|investigate|somewhat|various|appropriate|as needed)\b', re.I)

# Anything still carrying a placeholder is not a spec.
PLACEHOLDER = re.compile(r'(\bTBD\b|\bTODO\b|\bN/?A\b|to be determined|<!--|\{\{)', re.I)

# Any number at all. Deliberately not "number followed by a unit": real cards state
# thresholds as bare ratios (`ROC-AUC >= 0.65`, `<= 0.85 * baseline p50`) and a
# unit-hungry rule rejected most of the good ones. The comparator carries the weight.
NUMBER = re.compile(r'\d')
COMPARATOR = re.compile(
    r'(>=|<=|>|<|\bat least\b|\bat most\b|\bbeats?\b|\bfaster\b|\bslower\b|'
    r'\bhigher\b|\blower\b|\bfewer\b|\bmore\b|\bless\b|\breduces?\b|\bcuts?\b|'
    r'\bincreases?\b)', re.I)
WIN_CONDITION = re.compile(
    r'(\bwin\b|\bcounts as\b|\bthreshold\b|>=|<=|>|<|\bat least\b|\bat most\b|'
    r'\bbelow\b|\babove\b)', re.I)
PINNED_SAMPLE = re.compile(
    r'(/[\w.\-]+/[\w.\-/]*|\b[0-9a-f]{64}\b|\b\d+\s+\w+)')
# A line that plausibly runs, not a sentence about running: an interpreter, a script
# path, or an invocation carrying a flag. Prose passes a bare "word word word" regex,
# which is how "measure the judge the way we do today" once counted as a baseline.
COMMAND_LINE = re.compile(
    r'(?m)^\s*\$?\s*(?:(?:python3?|bash|sh|time|env|hyperfine|git|jq|curl|sqlite3|'
    r'make|pytest|node|cargo|go|ssh|\S+\.(?:py|sh))\s+\S+|[\w.~-]*/\S+|\S+\s+--\w)')

PROMPT = """You are writing the empirical spec for a Lumen pilot: a small, fixed-sample
experiment that answers ONE question with ONE number. Below is the deferred proposal
this pilot comes from. Write the spec the proposal implies. Do not invent scope the
proposal does not contain; if the proposal is thin, pick the smallest measurable
question it still supports.

TITLE: {title}

WHY IT WAS DEFERRED: {decision_reason}

CURRENT STATE: {current_state}

PROPOSED CHANGE: {proposed_change}

Answer with ONE JSON object and nothing else, with exactly these five string keys:

"hypothesis": one falsifiable sentence of the form "X beats Y on metric M by at
  least N <unit>". It must name a comparison and a numeric threshold. "X is
  promising" is a rejection.
"primary_metric": ONE number. Name it, name its unit, and state what counts as a
  win BEFORE the run (a comparator and a number).
"fixed_sample": exactly what both arms run against, PINNED — an absolute path, a
  row/query count, or a sha256. Both arms see the same sample or the run is void.
"baseline": the shell command(s) that measure what Lumen does today, run before the
  treatment exists so the baseline cannot be tuned to lose. Commands only, one per
  line, no prose.
"treatment": the shell command(s) for the smallest build that tests the hypothesis —
  not the full proposal. Commands only, one per line, no prose.

Absolute paths, no paid APIs, no Anthropic spend. The pilot runs on one box within
{wallclock} minutes."""

RETRY_SUFFIX = """

Your previous draft was REJECTED. Fix exactly these problems and answer again with
the same JSON object:
{problems}"""


class SpecResult:
    """Outcome of one authoring pass. `ok` is the only thing a caller should branch on."""

    def __init__(self, ok, sections=None, problems=None, attempts=0, raw=None):
        self.ok = ok
        self.sections = sections or {}
        self.problems = problems or []
        self.attempts = attempts
        self.raw = raw

    @property
    def banner_reason(self):
        return '; '.join(self.problems) if self.problems else 'no draft was produced'


def parse_draft(text):
    """Pull the JSON object out of a model reply. Returns {} on anything unusable."""
    if not text:
        return {}
    body = text.strip()
    fence = re.search(r'```(?:json)?\s*(.+?)```', body, re.S)
    if fence:
        body = fence.group(1).strip()
    start, end = body.find('{'), body.rfind('}')
    if start < 0 or end <= start:
        return {}
    try:
        obj = json.loads(body[start:end + 1])
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    return {k: str(v).strip() for k, v in obj.items()
            if k in SECTIONS and isinstance(v, (str, int, float))}


def validate(sections):
    """Every reason this draft is not a runnable spec. Empty list means it is one."""
    problems = []
    for key in SECTIONS:
        val = (sections.get(key) or '').strip()
        if len(val) < 20:
            problems.append(f'{key}: missing or too short to be a spec')
            continue
        if PLACEHOLDER.search(val):
            problems.append(f'{key}: still contains a placeholder (TBD/TODO/template comment)')
        # Hedging only disqualifies the two sections that must commit to a number.
        # A sample or an arm description may legitimately hedge about its context.
        if key in ('hypothesis', 'primary_metric') and VAGUE.search(val):
            problems.append(f'{key}: vague wording ("{VAGUE.search(val).group(0)}") — '
                            f'state a number, not a mood')

    hypothesis = (sections.get('hypothesis') or '').strip()
    if hypothesis:
        # No length rule: a good hypothesis often runs long because it spells out its
        # own falsifier (pilots/9d95766f5f43 does, at ~1.1k chars). Falsifiability is
        # policed by the comparator and the number, which is what actually matters.
        if not COMPARATOR.search(hypothesis):
            problems.append('hypothesis: not falsifiable — names no comparison (X beats Y)')
        if not NUMBER.search(hypothesis):
            problems.append('hypothesis: not falsifiable — names no numeric threshold')

    metric = (sections.get('primary_metric') or '').strip()
    if metric:
        if not NUMBER.search(metric):
            problems.append('primary_metric: no number to win or lose by')
        if not WIN_CONDITION.search(metric):
            problems.append('primary_metric: no win condition fixed before the run')

    sample = (sections.get('fixed_sample') or '').strip()
    if sample and not PINNED_SAMPLE.search(sample):
        problems.append('fixed_sample: not pinned — give a path, a count, or a sha256')

    for arm in ('baseline', 'treatment'):
        val = (sections.get(arm) or '').strip()
        if val and not COMMAND_LINE.search(val):
            problems.append(f'{arm}: no runnable command line')
    return problems


def author_spec(proposal, call, wallclock=90, max_attempts=MAX_ATTEMPTS):
    """Ask the gateway for the five spec sections. One retry carrying the rejections.

    `call(prompt) -> str | None`. A None (gateway down) is not a verdict: it ends the
    pass with ok=False and no sections, which fill_card renders as a loud banner.
    """
    prompt = PROMPT.format(
        title=(proposal.get('title') or '')[:200],
        decision_reason=(proposal.get('decision_reason') or '')[:600],
        current_state=(proposal.get('current_state') or '')[:800] or '(none recorded)',
        proposed_change=(proposal.get('proposed_change') or '')[:1200] or '(none recorded)',
        wallclock=wallclock)
    sections, problems, raw = {}, ['no draft was produced'], None
    for attempt in range(1, max_attempts + 1):
        ask = prompt if attempt == 1 else prompt + RETRY_SUFFIX.format(
            problems='\n'.join(f'- {p}' for p in problems))
        raw = call(ask)
        if raw is None:
            return SpecResult(False, sections, ['gateway unreachable'], attempt, raw)
        draft = parse_draft(raw)
        if not draft:
            sections, problems = {}, ['reply was not the requested JSON object']
            continue
        sections = draft
        problems = validate(draft)
        if not problems:
            return SpecResult(True, draft, [], attempt, raw)
    return SpecResult(False, sections, problems, max_attempts, raw)


def gateway_call(model=None, url=None, key_file='/home/lumen/agent/.env', timeout=240):
    """One synchronous LiteLLM call, shaped like `minerva_defer_review._rerun_direct`.

    Same gateway, same key file, same never-raises contract: a failure returns None,
    which is an infrastructure fault, not a verdict on the proposal. The key is read
    from a 0600 file and only ever placed in a header — never logged, never printed.
    """
    import urllib.request

    model = model or os.getenv('DEFER_SPEC_RUNG', 'deepseek-v4-flash')
    url = url or os.getenv('LITELLM_URL', 'http://localhost:4000/v1/chat/completions')

    def call(prompt):
        key = ''
        try:
            with open(key_file) as fh:
                for line in fh:
                    if line.startswith('LITELLM_MASTER_KEY='):
                        key = line.split('=', 1)[1].strip().strip('"\'')
                        break
        except Exception:
            pass
        body = json.dumps({
            'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 2000,
            'metadata': {'trace_name': 'minerva:pilot_spec',
                         'trace_metadata': {'service': 'minerva_defer_review',
                                            'stage': 'spec_author', 'ticket': 'wq2690'}},
        }).encode()
        hdr = {'Content-Type': 'application/json'}
        if key:
            hdr['Authorization'] = f'Bearer {key}'
        try:
            req = urllib.request.Request(url, data=body, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)['choices'][0]['message']['content']
        except Exception:
            return None

    return call


def _section_bounds(lines, heading):
    """(start, end) of a section's body — the lines between its heading and the next."""
    want = f'## {heading}'
    for i, line in enumerate(lines):
        if line.strip() == want:
            for j in range(i + 1, len(lines)):
                if lines[j].startswith('## '):
                    return i + 1, j
            return i + 1, len(lines)
    return None


def _body_for(key, text, ok):
    if key in ('baseline', 'treatment'):
        commands = text.strip() or '# NOT AUTHORED'
        return ['', '```', *commands.splitlines(), '```', '']
    body = text.strip() or 'NOT AUTHORED'
    prefix = [] if ok else ['> REJECTED DRAFT — do not run. See the banner above.', '']
    return ['', *prefix, *body.splitlines(), '']


FAILED_BANNER = (
    '> **SPEC AUTHORING FAILED (wq#2690) — DO NOT RUN THIS PILOT.** The card '
    'generator could not author a spec that passes its own checks, so the five '
    'spec sections below are a rejected draft, not a plan. A runner must NOT '
    'guess the missing measurement plan and must NOT hand-fill this card; block '
    'the ticket and cite this banner. Rejected because: {reason}.')


def apply_spec(card, result):
    """Write the five sections into a rendered card. Never leaves one blank."""
    lines = card.splitlines()
    for heading, key in HEADINGS.items():
        bounds = _section_bounds(lines, heading)
        if not bounds:
            continue
        start, end = bounds
        lines[start:end] = _body_for(key, result.sections.get(key, ''), result.ok)
    if not result.ok:
        banner = FAILED_BANNER.format(reason=result.banner_reason)
        insert = 1 if lines and lines[0].startswith('# ') else 0
        lines[insert:insert] = ['', banner]
    return '\n'.join(lines) + '\n'


def fill_card(card, proposal, call, wallclock=90):
    """Card in, complete card out. Returns (card_text, SpecResult)."""
    result = author_spec(proposal, call, wallclock=wallclock)
    return apply_spec(card, result), result


def unfilled_sections(card):
    """Which of the five spec sections are still empty or template-only.

    The audit the four stuck pilots needed and did not have: a card whose spec is
    unfilled is detectable without reading it.
    """
    lines = card.splitlines()
    unfilled = []
    for heading, key in HEADINGS.items():
        bounds = _section_bounds(lines, heading)
        if not bounds:
            unfilled.append(key)
            continue
        body = '\n'.join(lines[bounds[0]:bounds[1]])
        body = re.sub(r'<!--.*?-->', '', body, flags=re.S)
        body = body.replace('```', '').strip()
        if not body:
            unfilled.append(key)
    return unfilled
