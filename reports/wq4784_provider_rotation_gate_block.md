# wq#4784 — gemini-flash rotate-key: blocked on the attended rotation gate

## Symptom
LiteLLM upstream `gemini` has answered 401/403 since ~2026-10-30T10:34:54Z. CLASS 5
(`providers`) of fleet-selfheal correctly cooled down `gemini-flash` in
`dispatcher/runtime/backend_cooldown.json` (`"reason": "gemini 401/403 since 1793356494
(wq#3354)"`, `until: 1793402407`), so dispatch skips the rung — working as designed, not
itself a bug.

## What the ticket asks for
Rotate the gemini provider key, restart LiteLLM; fleet-selfheal re-enables the rung on the
first good probe (`ops/wq3354-fleet-selfheal/sh_providers.py`'s `recheck_interval_s`/
`disable_after_s` path).

## Why a headless worker can't do it
Any rotate-* tool invocation (including a bare mention of the tool's name in a Bash
command — ISSUE-LOG row 1261 documents this as a known over-match) is refused by
`harness/guard.sh`'s wq#3747 credential-rotation safety gate unless a same-day Vault
backup marker exists. That marker is created only by an attended operator holding the
Vault master password running the rotate tool's backup step directly — the gate's own
refusal text says so explicitly, and this ticket's attempt 1 was KILLED on the identical
block. ISSUE-LOG row 671 records the same finding for a different rotate-* tool
(`.git-credentials*` redaction): three independent layers (directory deny-list, guard.sh
text match, CLAUDE.md Tier-0) agree this class of action is deliberately attended-only.

## What this attempt did instead
- Confirmed the cooldown is real and current (see Symptom above; re-read
  `dispatcher/runtime/backend_cooldown.json` live, not from a stale snapshot).
- Added `providers.notes.gemini-flash` to `ops/wq3354-fleet-selfheal/selfheal.yaml`
  (lines 75-90), mirroring the existing `nodes.notes` pattern (wq#3619), so the next page
  on this rung carries this context instead of re-deriving it. Opened as PR#2566
  (claude-foreman). Not deployed to the live config
  (`/home/lumen/agents/fleet-selfheal/selfheal.yaml`) from this session — that path is
  outside this worker's granted directories and the ticket carries no `needs:run/copy`
  grant, so deployment is itself an attended/follow-up step, same as the rotation.
- Appended ISSUE-LOG row 1639 with this finding.
- `wq block 4784` as the wq#1676 physical-task escape hatch: no deterministic check
  exists for "an attended operator ran the rotate+restart," so this is the intended
  non-stalled-ticket outcome, not a stuck worker.

## Recommended real fix (attended operator)
1. Run the rotate tool's Vault backup step for the gemini provider key (pastes the OLD
   value, unlocks Vault with the operator's own master password).
2. Rotate the key, restart LiteLLM.
3. No further ticket needed — fleet-selfheal's existing recheck path clears the cooldown
   on the first good probe once the key works again.
