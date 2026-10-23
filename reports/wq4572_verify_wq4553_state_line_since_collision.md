# wq#4572 verify-pass (archetype 8): wq#4553 (wq#4539 phase 1) — context_builder.py roster+episodes

**Target:** PR `claude-foreman#2407` (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2407),
commit `6095ce92` on this branch's history (`ops/wq4553-context-builder-live-roster/`). Author:
claude-sonnet-5 (wq#4553). Verifier: claude-sonnet-5@high, fresh instance (wq#4572).

**Verdict: CONTRADICT.**

## What the PR claims

`roster()`/`parse_state_lines()` in `ops/wq4553-context-builder-live-roster/context_builder.py`
read `lumen_state.py current` output live and parse each `subject.predicate = value (since
DATE)  [tag]` line via `_STATE_LINE` (lines 75-77). The README's own "Evidence" section claims:

- `pytest test_live_roster.py` → 7 passed.
- Live replay: `roster()` is byte-stable across two calls, excludes the four retired Macs,
  keeps non-retired PG-stale hosts (thor/sekhmet/ptah/anubis).
- The one bug the PR documents and fixes: `fleet_context.py`'s original regex anchors
  `\(since DATE\)\s*$` (end of line), which matches 0/1244 live lines because every line now
  carries a trailing confidence tag after the date. This bundle's `_STATE_LINE` drops that end
  anchor so it matches regardless of what follows.

## Reproduced, holds

- `python3 -m pytest -q test_live_roster.py` from this worktree → **7 passed**, matches the PR
  exactly.
- Read the full diff against `ops/wq4537-claude-memory-hand-prose/context_builder.py` (the
  prior bundle in the same lineage) to confirm `_STATE_LINE`, `roster()`, `episodes()`,
  `_is_retired()` are net-new code from this PR, not inherited unchanged — in scope for this
  verify-pass.
- The documented end-anchor fix is real and correct: dropping `\s*$` does let the regex match
  lines with a trailing `[UNVERIFIABLE — no probe]`/`[verified Nd ago]` tag.
- `episodes()`'s SQL is parameterized (`LIMIT ?`), the revoked/superseded/merged-note exclusion
  logic matches `test_episodes_filters_revoked_superseded_merged_and_kind`.
- Retired-node exclusion (`_is_retired`, word-boundary anchored `^(retired|decommissioned)\b`,
  case-insensitive) is correctly scoped and does not false-positive on prose like "Retiring
  soon" (no match — the check is the literal word "retired", not a prefix).

## Contradiction: `_STATE_LINE`'s non-greedy value group silently mis-parses on a plausible input

`_STATE_LINE` (context_builder.py:76-77):

```python
r"^\s*(?P<subject>[\w-]+)\.(?P<predicate>[\w-]+)\s*= (?P<value>.*?)\s*"
r"\(since (?P<since>\d{4}-\d{2}-\d{2})\)")
```

`(?P<value>.*?)` is **non-greedy**. Because the regex is not end-anchored (the PR's own fix),
Python's `re.match` stops the value group at the **first** substring on the line that looks
like `(since YYYY-MM-DD)`, not the real trailing one. If a status/role free-text value itself
contains a `(since DATE)`-shaped fragment — a plausible shape, since "(since DATE)" is an
idiom this fleet's own prose uses constantly (6 literal occurrences in `kb/ISSUE-LOG.md` alone;
the design doc for this same ticket, `design/wq4539_one_render_one_truth.md:18,51`, uses the
bare idiom twice) — the parser truncates the value and reports the wrong `since` date.

Reproduced directly against the shipped module (not a hypothetical — ran the actual
`parse_state_lines`):

```
$ python3 -c "
import importlib.util, sys, types
spec = importlib.util.spec_from_file_location('cb', 'context_builder.py')
mod = importlib.util.module_from_spec(spec)
sys.modules['fm_db'] = types.SimpleNamespace(connect=lambda: None)
spec.loader.exec_module(mod)
line = 'testhost.status         = flaky (since 2020-01-01) but recovered  (since 2026-10-11)  [UNVERIFIABLE]'
print(mod.parse_state_lines(line))
"
[{'subject': 'testhost', 'predicate': 'status', 'value': 'flaky', 'since': '2020-01-01'}]
```

Expected (and what a greedy `(?P<value>.*)` group gives, confirmed separately, not shipped):
`value='flaky (since 2020-01-01) but recovered'`, `since='2026-10-11'` — the real, trailing
since-clause that `lumen_state.py` actually appended to this line. The shipped parser instead
reports the status as the one-word stub `"flaky"` with a `since` date six years stale, and
`build()` renders that truncated, wrong-dated text into `claude_memory.md`'s FLEET DEVICES
section with no error, no warning, and no test that would catch it — `test_live_roster.py`'s
fixture (`fixtures/state_sample_2026-10-23.txt`) has no line whose value contains an embedded
`(since DATE)` fragment, so this path is untested.

This is squarely inside the ticket's own premise: wq#4539 phase 1 is "one render, one truth" —
the entire point is that `roster()` must render **accurate** live facts instead of frozen PG
staleness. A parser that silently corrupts a correctly-formatted live line (wrong value, wrong
since-date, no error surfaced) defeats that premise for exactly the free-text fields (`role`,
`status`) most likely to contain incidental parenthetical dates, since those are the two fields
written as prose by Frank/agents narrating incidents — the same voice that produced the 6
`(since DATE)` idioms already found in `kb/ISSUE-LOG.md`.

I could not check whether this trap is live in `minerva.db` **today**: `/home/lumen/minerva` is
outside this BUILD worker's `--add-dir` grant (confirmed directly, see below, matching the PR's
own README claim), and the `mcp__fleet-state__*` tools that could have queried it read-only are
not in this session's MCP allowlist (see Capability gaps). The finding stands independent of
that: it is a reproducible defect in the shipped parsing function against a well-formed,
idiomatic input, not a claim about current `minerva.db` contents.

## Attempts to break the fix that did NOT find a defect

- `roster()`'s dict-overwrite-by-subject/predicate grouping: fine as long as `lumen_state.py
  current` emits one line per subject.predicate (its own contract, outside this PR's file).
- Retired-node filter word-boundary regex: no false positive on "Retiring"/"retire-" style text.
- `episodes()` SQL injection / error handling: parameterized, fails open to `[]` on any
  `sqlite3.Error` (consistent, deliberate "fail open" design — see `test_episodes_missing_db_fails_open`).
- Hand-prose preservation: unchanged from wq#4537, out of this PR's diff (confirmed via diff
  against `ops/wq4537-claude-memory-hand-prose/context_builder.py`).
- `_rationale()`/decisions rendering (PERMANENT DECISIONS / STANDING RULES / FRANK CORRECTIONS):
  untouched by this diff.

## Liveness — could not run the ticket's mandated probe

The ticket instructs: `bash /home/lumen/foreman/ops/wq4553-context-builder-live-roster/install.sh --verify`
(exit 0 = live). Both forms refused, **not retryable from this lane**:

- `bash /home/lumen/foreman/ops/wq4553-context-builder-live-roster/install.sh --verify` →
  `This command requires approval` (no `harness/worker-settings.json` allow entry covers
  `bash <absolute path>`; only `Bash(bash scripts/*)` is allowed).
- `/home/lumen/foreman/ops/wq4553-context-builder-live-roster/install.sh --verify` (direct exec,
  file is `+x`) → same refusal (no allow entry for the bare path either).
- Even with the `bash` prefix allowed, the script's `vfile` check inside `--verify` reads
  `/home/lumen/minerva/memory/context_builder.py`, which is outside this session's granted
  directories — confirmed directly: `cat /home/lumen/minerva/memory/context_builder.py` →
  `was blocked. For security, Claude Code may only concatenate files from the allowed working
  directories for this session`. This matches the PR's own README claim verbatim ("even running
  install.sh here is impossible").

So the liveness question is genuinely unanswerable from a BUILD worker's sandbox, by design
(wq#1719/wq#900 walls BUILD out of `/home/lumen/minerva`) — not something to work around. Per the
dispatch doctrine, flagging the exact refused command shapes above rather than retrying them.

## Capability gaps hit (not retried)

- `mcp__gitea__pull_request_read` and `mcp__fleet-state__{search_objects,execute_sql}` both
  returned "haven't granted permission yet" — neither is in `harness/worker-settings.json`'s MCP
  allow list (`mcp__keenable__*` only), and this is a headless session with no human to answer an
  approval prompt, so these are a standing gap, not a one-off miss. Worked around for the PR diff
  by reading it directly out of this worktree's git history (the commit is already on this
  branch) instead of via Gitea; worked around for "is this parser risk live" by reasoning from
  `kb/ISSUE-LOG.md`'s own prose idiom frequency instead of querying `minerva.db`.

## Actions taken

| Action | Rationale |
|---|---|
| `kb/ISSUE-LOG.md` row appended | Records the contradiction and the two capability gaps, per doctrine |
| Judgment ticket filed, pri 1, `deps=4553`, `model=claude-opus-5`, `effort=high` | Archetype-8 escalation per this ticket's own protocol ("escalate opus-5@high on contradiction"): decide whether to make the value group greedy (`(?P<value>.*)` instead of `.*?` — confirmed separately to pick the correct trailing since-clause) or require a stronger anchor, and whether to check `minerva.db` for any live value already containing an embedded `(since DATE)` fragment before deciding urgency |
| `wq#4572 done` | This verify-pass's own deliverable (confirm or contradict) is complete; the fix itself is not re-implemented here per archetype 8 |
