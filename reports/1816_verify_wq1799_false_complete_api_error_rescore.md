# wq#1816 — Adversarial verify-pass of wq#1799 (re-score 112 FALSE_COMPLETE API-error attempts)

**Verdict: CONTRADICT** — the classifier scrub is keyed on the wrong signal. It reads
`state.last_assistant_text` (the last *text*-type content block) instead of checking
whether the retry banner was truly the end of the session. Any attempt where the model
recovers from the transient API error and finishes the ticket using tool calls only
(Edit/Bash/etc., with no further assistant *text* block before the terminal `result`
frame — extremely common for a headless worker whose CLAUDE.md tells it to keep final
text minimal) gets scrubbed from `SUCCESS` to `CRASH`, even though the session was a
genuine, verifiable success. That is the same failure class wq#1799 was hired to fix,
now in the opposite direction: a real success gets mis-charged as a failure.

## What was shipped

PR #506 (merged `e68adfc`, squash of `5b572d1`). `harness/classifier.py`:
- `RunState.observe()` tracks `last_assistant_text` — the last non-empty **text-type**
  content block seen on any `assistant` frame (`classifier.py:195-199`), unconditionally
  overwritten on every such block, with no memory of what happened *after* it.
- `classify()`'s §C1 SUCCESS branch scrubs to `CRASH` if that text matches
  `^\s*\[?API Error: ` (`classifier.py:333-334`), before the frame is trusted as
  `SUCCESS`.
- Three new unit tests in `harness/tests/test_classifier.py`, plus E.1 (historical
  re-score) and E.3 (`api_retries` undercount) write-ups in
  `reports/wq1799_rescore_false_complete_api_errors.md`.

## Probe run

Re-ran the shipped E.1/E.3 probe verbatim: `python3
reports/wq1799_rescore_false_complete_api_errors.py` reproduces the report's numbers
exactly (115 FALSE_COMPLETE token-pool attempts, 62 with no retrievable
last-assistant-text, 53 checkable, 0 matches). `python3 -m pytest
harness/tests/test_classifier.py -q`: 44 passed, including the three new API-error
tests. The regex itself is not guessed — it is copied verbatim from
`research/FWORKER_FIXES_FROM_HR_TEARDOWN.md:14`'s prescribed pattern. E.1's honest
reporting of the 62-attempt data gap (rather than guessing a verdict) matches
DOCTRINE. All of that is solid and I have no dispute with it.

Then I tried to break the fix's actual scrub logic with an adversarial frame sequence
the shipped tests never construct: an API-error banner followed by **real recovery
work that never emits another text block**, only tool_use turns, before a normal
success result frame:

```python
frames = [
    {"type": "system", "subtype": "init", "session_id": "s1"},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "API Error: 500 litellm.InternalServerError"}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Edit", "input": {}}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {}}]}},
    {"type": "result", "subtype": "success", "is_error": False, "result": "done, landed the fix"},
]
st = RunState()
for f in frames: st.observe(f)
classify(st, exit_code=0, elapsed_s=120, diff_is_empty=False)
```

Result: `CRASH` (turns=3, exit_code=0, diff non-empty — every other signal says real
work landed). Expected: `SUCCESS`. The shipped
`test_api_error_text_not_in_last_block_still_scores_success` test only covers recovery
that ends in *another text block* (`"done, PR opened"`); it never exercises a
tool-only recovery tail, which is the far more common shape for this fleet's headless
workers (CLAUDE.md's own output-discipline rule discourages a closing prose summary —
the last content emitted before `wq done`/a final commit is routinely a tool call, not
text).

## Why this is worse than the bug being fixed, not just an edge case

Because `classify()` now returns `CRASH` instead of `SUCCESS` for this shape, the
attempt never reaches `fworker.py`'s own safety net: the artifact/evidence
verification gate at `fworker.py:1874-1913` (`verify_done` / `check_artifacts_done`,
which would confirm the ticket's real acceptance criteria landed and call `wq_done`
directly) is gated on `classification in (SUCCESS, DEGRADED_SUCCESS)` and is skipped
entirely for `CRASH`. Instead `reconcile()` routes `CRASH` to `handle_failure()`
(`fworker.py:2306`, `2465-2492`), which is **not** in the resumable set
(`FALSE_COMPLETE, INDETERMINATE, AUTH, HANG` — `fworker.py:2302`) and always starts a
**fresh worktree with no session resume** (`fworker.py:1708-1712`, no
`resume_session`/`prior_worktree` passed) — discarding the very work that was just
verified to exist by turns/exit-code/diff. With `MAX_ATTEMPTS = 2`
(`fworker.py:275`), a ticket that hits this shape on attempt 1 gets one fresh, from-
scratch redo; if that redo *also* ends in a text-less tool-only finish (plausible for
the same ticket, same worker style), attempt 2 exhausts and the ticket is hard-blocked
needing Frank (`handle_failure` -> `classify_block_reason(CRASH)` = `WORK_BLOCKED`,
"always needs Frank; never auto-requeued") — for a ticket that was, per the classifier's
own turns/exit/diff signals, genuinely done. wq#1799's stated goal was "stop charging
the model for an infra hiccup it never actually failed at" (commit `5b572d1`); this
scrub can now charge the model (and Frank's queue) for a *success* it did actually
land.

## What a fix needs

The scrub needs a stronger predicate than "the last text block matches the banner
regex" — e.g. also requiring no `turns` advanced after the matching block (would need
a new counter, since `last_assistant_text` currently has no positional/ordinal
information relative to `state.turns`), or reading `state.result_text` (the CLI's own
terminal result string, independent of the assistant-frame text stream) as the primary
signal and using `last_assistant_text` only as corroboration, not the sole gate.

## Scope note (not blocking, but worth a follow-up ticket)

Separately: routing this shape to `CRASH` (vs. e.g. `INDETERMINATE`, which the fix's
own report frames as "no real work happened, an infra hiccup") forfeits the
same-session resume (`fworker.py:2276-2280`) that `FALSE_COMPLETE` gave these attempts
*before* this fix — even in the case where the scrub is correctly triggered (banner
truly is the last content, no recovery happened at all). `CRASH`'s only next step is a
brand-new worktree from scratch. That mirrors the pre-existing litellm-gateway `CRASH`
convention for the identical text shape, so it isn't a new inconsistency this PR
introduces, but the PR never examines whether resume would be cheaper for a
transient/infra-attributed failure — worth a follow-up, not a blocker on its own.

## Verdict

**CONTRADICT.** The classifier fix correctly stops one false-SUCCESS->FALSE_COMPLETE
path (confirmed: regex matches the prescribed HR pattern, tests pass, E.1/E.3
re-verified with identical numbers), but its choice of signal (last *text* block,
ungated on subsequent tool-only activity) opens a new false-SUCCESS->CRASH path that a
headless worker's normal tool-only completion style will hit in practice, and that
path is *more* expensive than the bug being fixed (fresh-worktree redo or a hard
Frank-block, instead of a downstream artifact check that would have confirmed the
ticket was actually done). Escalates to opus-5@high per archetype-8.
