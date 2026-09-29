## What

`fworker.py`'s episode/archive path recorded a legitimate `wq block` (or a
lost claim) as episode kind `code-ship` with classification SUCCESS,
because the sentinel cross-check confirmed the block but never wrote that
back into `classification` before it reached the attempt record, charging,
and the episode event.

## Why

wq#3114 blocked itself (chrome-mcp-oauth-worker outside any granted
`--add-dir`) and made no change, yet `lumen_episode` archived "attempt 1
SUCCESS: ... add a HEAD-only deploy..." (kind `code-ship`). A later
precheck/recall then reports the blocked work as done — exactly the false
signal wq#3116 exists to close.

## How

After `sentinel_confirmed_block`/`sentinel_confirmed_claim_error` are
computed (harness/fworker.py:3867-3877), override `classification` to
`BLOCK_SENTINEL`/`CLAIM_ERROR_SENTINEL` before anything downstream reads
it — the attempt record, `charging.classify_charge`, and
`episodic.digest_events` (which keys `kind: code-ship` off SUCCESS/
DEGRADED_SUCCESS). `raw_classification` is untouched for RCA.

## Testing

`episodic.digest_events` fed an attempt_end event with
`classification=BLOCKED` now produces `kind: session` (previously an
unpatched SUCCESS would have produced `code-ship`) — confirms the fix
closes the false-success path without needing a full fworker run.
`fworker.py verify-evidence` passes for this ticket's evidence.json.

## Risk / rollback

Low: the change only re-labels an already-detected block/claim-race case
that was previously excluded from the FALSE_COMPLETE checks by the same
sentinel flags; charging's fallthrough (`CHARGE_TICKET, cause`) keeps the
prior charge outcome for this case. Revert is a single-hunk git revert.

## Out of scope

Correcting wq#3114's existing wrong archival note, and granting
`chrome-mcp-oauth-worker` access or a documented owner (ISSUE-LOG 832),
both need minerva/orchestrator access not available to a build worktree —
logged as ISSUE-LOG #1038 and left open.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
