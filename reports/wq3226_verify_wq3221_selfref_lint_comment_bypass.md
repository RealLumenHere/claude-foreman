# wq#3226 verify: wq#3221 LiteLLM restart/upgrade verify must assert a trace lands

Verdict: **CONTRADICT** (escalates per ticket policy to opus-5@high judgment)

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1488, branch
`feat/wq3221-litellm-restart-upgrade-verify-must-asse-a2`, tip `70a1af37`
(not yet merged to `main`). Diff verified: `git show 70a1af37` — 10 files
changed, 942 insertions / 3 deletions, across `harness/langfuse_trace_rate_monitor.py`,
`ops/wq2903-model-ref-drift/model_ref_lint.py`,
`ops/wq3169-litellm-completion-probe/litellm-completion-probe`, and the new
`ops/wq3221-litellm-restart-verify-langfuse/` bundle (README, patch, fixture,
tests).

## What wq#3221/PR#1488 claimed

Four parts, follow-up to wq#3211's 47h silent LiteLLM→Langfuse trace loss:

1. `maint-ops/litellm-upgrade`'s `verify()` (patched via unified diff since that
   script lives in the separate `/home/lumen/bin` repo) polls Langfuse's own
   `/api/public/traces` API after the existing completion smoke check and fails
   `verify()`/triggers rollback if no `litellm-acompletion` trace lands within 60s.
2. `harness/langfuse_trace_rate_monitor.py` opens one `[fleet-fires]` ticket when
   a trace-rate floor breach persists past 2h, so a repeatedly-bumped `aq` row
   can't go unassigned the way `aq 2492` did (101 bumps, never escalated).
3. `litellm-completion-probe` polls the same trace API once a completion
   succeeds, catching the loss within one 30-min probe cycle.
4. `ops/wq2903-model-ref-drift/model_ref_lint.py` gains a `SELF_REF` finding
   kind that "**flags ANY self-referential entry in the block, regardless of
   provider**" (`model_ref_lint.py:63`) — catching the `LANGFUSE_PUBLIC_KEY:
   LANGFUSE_PUBLIC_KEY` config trap (bare `KEY: KEY` instead of `KEY:
   os.environ/KEY`) that caused the 47h loss, at edit time, before the next
   restart applies it.

"44 new/updated unit tests, including TEST TO FAILURE canaries for both the
trace assertion and the config lint."

## What I checked

Checked out `70a1af37` into a scratch worktree (`git worktree add
/tmp/fworker-wq3226/pr-check 70a1af37 --detach`) and ran the exact suite the
README specifies:

```
python3 -m pytest harness/tests/test_langfuse_trace_rate_monitor.py \
  ops/wq3169-litellm-completion-probe/tests/test_litellm_completion_probe.py \
  ops/wq2903-model-ref-drift/tests/test_model_ref_lint.py \
  ops/wq3221-litellm-restart-verify-langfuse/tests/test_patch_applies_and_verifies.py -q
```
→ `44 passed in 9.21s`. Matches the claimed count.

Part (1) holds up under adversarial reading:
- `diff /home/lumen/bin/maint-ops/litellm-upgrade
  ops/.../patches/fixtures/maint-ops-litellm-upgrade.pre-wq3221` → identical
  (exit 0): the pinned fixture the patch tests apply against is not stale —
  `git apply --check` of the patch against a fresh copy of the **live** file
  also succeeds (exit 0), so the "applies cleanly" claim is true against
  reality today, not just against a frozen fixture.
- `langfuse_trace_seen()`'s TEST TO FAILURE case
  (`test_langfuse_trace_seen_false_when_no_trace_within_timeout`) is real: a
  Langfuse response with `totalItems: 0` correctly returns `False`, which sets
  `ok = False` in the heredoc and would fail `verify()`. The true-path test is
  symmetric. Part (2)'s `escalate_if_persistent()` tests (first-breach /
  threshold / no-re-ticket / recovery-clears-state / wq_add-failure-retries)
  cover the state machine correctly, and part (3)'s probe changes to
  `litellm-completion-probe` fail-close on a real timeout and only skip
  (`ok=True`) when creds are provably absent, which is a narrower and better
  scoped soft-fail than what follows below.

## Contradiction found (CONFIRMED)

**`find_env_self_refs()` (`ops/wq2903-model-ref-drift/model_ref_lint.py:94-127`)
silently stops scanning — or silently fails to match — the moment the
`environment_variables:` block contains an ordinary YAML comment**, directly
contradicting the shipped claim at `model_ref_lint.py:63` that it "flags ANY
self-referential entry in the block, regardless of provider."

Two independent break shapes, both reproduced against the actual shipped
function (not a rewritten copy):

**(a) A comment line between entries ends the block early.** `ENV_BLOCK_LINE_RE`
(`model_ref_lint.py:67`) requires a line to match `key: value`; a `#`-comment
line doesn't, so `model_ref_lint.py:118-120` treats it as "dedented to a new
top-level key" and sets `in_block = False` — every entry after the comment,
however deeply self-referential, is never even looked at:

```
$ python3 /tmp/fworker-wq3226/probe_comment.py
findings: []
```
against:
```yaml
environment_variables:
  CEREBRAS_API_KEY: os.environ/CEREBRAS_API_KEY
  # Langfuse observability creds (rotated 2026-09-01)
  LANGFUSE_PUBLIC_KEY: LANGFUSE_PUBLIC_KEY
  LANGFUSE_SECRET_KEY: LANGFUSE_SECRET_KEY
```
Both LANGFUSE self-refs — the exact wq#3211 shape — go unflagged; `lint()`
would exit 0.

**(b) A trailing inline comment on the same line breaks the equality check.**
`ENV_BLOCK_LINE_RE`'s value group `(.+?)\s*$` captures everything up to
end-of-line, comment included; `model_ref_lint.py:126`'s
`val == key` then compares `"LANGFUSE_PUBLIC_KEY  # rotated 2026-09-01"` against
`"LANGFUSE_PUBLIC_KEY"` and finds them unequal:

```
$ python3 /tmp/fworker-wq3226/probe_inline.py
findings: [{'kind': 'SELF_REF', 'model': 'LANGFUSE_SECRET_KEY', 'line': 7, ...}]
```
against:
```yaml
environment_variables:
  LANGFUSE_PUBLIC_KEY: LANGFUSE_PUBLIC_KEY  # rotated 2026-09-01
  LANGFUSE_SECRET_KEY: LANGFUSE_SECRET_KEY
```
Only the second, comment-free line is caught; the first (with an entirely
ordinary annotation) is silently missed.

Both probes are re-runnable, deterministic, no live-file dependency
(`/tmp/fworker-wq3226/probe_comment.py`, `/tmp/fworker-wq3226/probe_inline.py`
— synthetic fixtures written inline, matching acceptance-probe doctrine).

**Why this matters, not just "an edge case the tests didn't hit":** all four
of the shipped `SELF_REF` tests in
`ops/wq2903-model-ref-drift/tests/test_model_ref_lint.py:72-135` build their
`environment_variables:` fixture from a bare list of `key: value` lines with
no comment anywhere — the one shape the parser handles correctly. None of
them is drawn from outside that population (wq#2630's falsifiability
doctrine: a probe that can't fail proves nothing). Comments in exactly this
kind of ops YAML are not a hypothetical: this same PR's own three other files
(`langfuse_trace_rate_monitor.py`, the `verify()` patch,
`litellm-completion-probe`) are saturated with inline `# wq#NNNN: ...`
annotations as house style, and `kb/ISSUE-LOG.md#904` — the row this exact
config block was already flagged under once — is itself a comment-shaped
annotation convention. A config author who does what this ticket's own README
recommends ("rotate the key... replace the literal") and leaves a one-line
`# rotated <date>` note next to the entry they just fixed — the single most
natural thing to write at that exact spot — defeats the lint that exists
specifically to catch that entry if it's wrong. This is the ticket's own
part-(3) protection failing on exactly the artifact (a hand-edited
`environment_variables:` block) it was built to guard.

## What I did not find broken

Parts (1), (2), and the `litellm-completion-probe` half of (3) hold up under
the same adversarial pass — tests are real (not tautological), the shipped
patch applies to the live file today, and the fail-closed/fail-open
boundaries in the trace-assertion and escalation code are reasoned and
tested correctly. The regex-based credential parsing in part (1)
(`langfuse_trace_seen()`'s `^LANGFUSE_PUBLIC_KEY=(\S+)$`, stricter than the
codebase's existing `load_creds()`/`read_master_key()` conventions for the
same file) is a minor robustness gap worth a follow-up look, but
`docker-compose`'s `env_file:` directive (which `agent/.env` is loaded
through per this PR's own README) doesn't support shell `export` prefixes or
quoting, so it's very unlikely to be live-exploitable today — noting it, not
escalating on it.

## Action taken

Contradiction posted to wq#3221 via `wq note`. Not re-implementing
`find_env_self_refs()` per ticket instruction ("Do NOT re-implement;
verify"). Fix for whoever picks up the follow-up: strip a `#`-comment tail
before the value-equality check (`model_ref_lint.py:126`), and don't treat a
comment-only line as ending the block (only a genuinely dedented/non-matching
*content* line should); add fixture coverage for both shapes so the four
existing `SELF_REF` tests stop being drawn entirely from the one population
the parser already handles.
