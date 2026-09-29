# wq#3126 — judgment: wq#3123's fix (PR#1401) contradicted

Verifier: claude-sonnet-5@high, worktree `wq3126-a1`, 2026-09-29 (fresh instance, same model
as wq#3123's author claude-opus-5 — model differs, effort matches per verify_pass routing).
Target: `harness/pilot_spec_author.py` (wq#3123's `3258d56a2e`, PR#1401) against the false-
positive class wq#3121 reported (whole-line `#` comments and URL path components read as
local filesystem paths by `_referenced_paths`/`lint_card`).

## Ruling — contradiction UPHELD (partial)

wq#3123's fix is real and its own 3 probes are honest: `COMMENT_LINE` correctly drops a line
whose *entire* stripped text is a `#` comment, and `URL_SPAN` correctly blanks a `scheme://`
span before token extraction, while still reading a real path sharing a `curl` line. Verified
by re-running the shipped suite unchanged: `python3 -m pytest
harness/tests/test_pilot_spec_author.py -q` → 17/17 pass.

But the fix scoped "comment" to *whole-line* comments only (`COMMENT_LINE =
re.compile(r'^\s*\$?\s*#')`, anchored at line start), which is narrower than the defect class
wq#3123 itself was opened to close ("false-positives on URL/API-route text ... including
inside # comments"). A **trailing** `#` comment on a real command line is not a prose block —
it is not skipped, and a path-shaped token inside it is still misread as a local file. This is
not a hypothetical: the repo's own pilot-card format already uses this exact shape outside any
test population.

**Pinned fixture, not a live mutable file**: `pilots/0eed2f5bdb92/CARD.md:81-82` (`##
Treatment` section, `_section_bounds` matches lines 72-85), committed, unrelated to this
ticket:

```
python3 /home/lumen/foreman/pilots/0eed2f5bdb92/run_pilot.py    # arms A, B, C
python3 /home/lumen/foreman/pilots/0eed2f5bdb92/significance.py # paired McNemar A vs C
```

That specific card's trailing comments don't happen to contain a path-shaped token, so it
doesn't itself trip the bug — but it proves the shape (`<real command>  # <prose>`) is one this
codebase already authors, not an edge case invented for the probe. A minimal, deterministic,
re-runnable script built on that exact shape (`/tmp/fworker-wq3126/probe_inline_comment.py`,
reproduced inline below) falsifies the "does not read comments" claim through the public
`lint_card()` API:

```python
import sys
sys.path.insert(0, '/home/lumen/foreman/harness')
import pilot_spec_author as psa

card = (
    "## Baseline (measure FIRST)\n\n"
    "```\n"
    "python3 run_pilot.py  # see /api/v1/users for the schema\n"
    "```\n\n"
    "## Treatment\n\n"
    "```\n"
    "python3 run_pilot.py --treatment\n"
    "```\n"
)
print(psa.lint_card(card, '/home/lumen/foreman'))
```

Result on the shipped fix (`3258d56a2e`, HEAD of main at claim time):

```
['baseline: /api/v1/users does not resolve']
```

Root cause: `_referenced_paths` only calls `COMMENT_LINE.match(line)` — `re.match` anchors at
position 0, so a comment marker anywhere but the start of the (optionally `$`-prefixed) line
never matches. `URL_SPAN` does not help either since `# see /api/v1/users for the schema`
contains no `scheme://`. The remaining `_PATH_TOKEN.findall` on `python3 run_pilot.py  # see
/api/v1/users for the schema` reads `/api/v1/users` exactly as it read `/chat/completions`
before wq#3123 — the same defect, a different comment position.

This is the same failure shape wq#3121 flagged in wq#3118: a fix whose test population (3
new probes, all whole-line comments or bare URLs) matches its own scoping decision and so
cannot falsify a scoping decision that was too narrow. wq#3123's 17/17 is not wrong, it is
just not sufficient — the same lesson wq#3123's own commit message drew about wq#3118's
14/14.

## Not re-implemented

Per this ticket's instruction, no fix was written here. `harness/pilot_spec_author.py` and
`harness/tests/test_pilot_spec_author.py` are unmodified in this worktree; the probe lives
only under `/tmp/fworker-wq3126/` (scratch, not committed).

## Escalation

Filed per `verify_pass.escalate_on_contradiction`: follow-up fix ticket wq#3129
(model=claude-opus-5@high, archetype=standard-engineering, deps=none, refs wq#3126/wq#3123),
mirroring how wq#3121's contradiction of wq#3118 produced wq#3123. wq#3123 noted directly
(`wq note 3123 ...`) since it is `status=done` and `wq review` only accepts `pending_review`.
ISSUE-LOG row 1047 appended.

## Acceptance evidence

- `python3 -m pytest harness/tests/test_pilot_spec_author.py -q` → 17/17 pass (fix's own
  probes still green; not the thing in dispute).
- `python3 /tmp/fworker-wq3126/probe_inline_comment.py` → `['baseline: /api/v1/users does
  not resolve']` against the shipped `3258d56a2e`, on a card shape (`command  # trailing
  prose`) proven to already exist in a committed, unrelated fixture
  (`pilots/0eed2f5bdb92/CARD.md:81-82`), so the probe is outside the fix's own design
  population per the wq#2630 doctrine and can fail (and does).
