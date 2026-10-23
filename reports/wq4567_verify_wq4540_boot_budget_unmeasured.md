# wq#4567 verify-pass (archetype 8): wq#4540 boot budget — claude_context.sh --core

**Target:** PR `claude-foreman#2403` (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2403,
branch `feat/wq4540-boot-budget-claude-context-sh-core-is-89-a1`, head `e2a32d90`, **unmerged**
at verify time). Author: claude-sonnet-5 (wq#4540). Verifier: claude-sonnet-5@high, fresh
instance (wq#4567).

**Verdict: CONTRADICT.**

## What the ticket requires

wq#4540's own acceptance test, verbatim from the ticket `detail` field:

> Measure: `export WQ_AGENT=x; bash claude_context.sh --core | wc -c`. Target <=45 KB.

The ticket title names the defect as a measured number: `claude_context.sh --core is 89,865 B
(~22K tok) not ~2K`. The deliverable is a reduction in that measured number, not a capability
that could produce one if someone else later wires it in.

## What the diff actually ships

1. `harness/instincts_render.py` gains a third `--audience boot` mode (tier 0+1 whole, tier 2
   as bare `[trigger]` titles, no rule text). Real code, real tests, in this repo. Reproduced:
   `pytest harness/tests/test_instincts_render.py` → **9 passed** (matches the PR's own claim)
   from a clean detached extraction of `e2a32d90` (`git worktree add --detach
   /tmp/fworker-wq4567/wq4540wt e2a32d90`, since this BUILD lane's worktree has no fs grant to
   `/home/lumen/worktrees/wq4540-a1`).
2. `ops/wq4540-boot-budget-claude-context/` — `fleet-tools-render --boot`,
   `live_fleet_state_cap.py`, `handoff_s3_rewrite.py`, 14 tests, `install.sh`. Reproduced:
   `pytest ops/wq4540-boot-budget-claude-context/` → **14 passed** (matches). All tested
   against fixtures, not the live files.
3. **Nothing in the diff touches `/home/lumen/minerva/context/claude_context.sh`** — the file
   the ticket's own acceptance probe measures. `install.sh` step 4 does not apply the wiring
   edit either; it `cat <<'EOF'`s two `sed` one-liners for a human/session-with-access to run
   by hand, and the done-notes confirm: `[UNMERGED-PR: PR #2403 open, not yet merged]`.

## Verification performed

- Confirmed `/home/lumen/bin/claude_context.sh` (readable, in this dispatch's `--add-dir`
  grant) is a 2-line wrapper: `exec bash "/home/lumen/minerva/context/claude_context.sh" "$@"`
  — the real logic, and the real byte count, live entirely in a file this diff never
  touches and this session has no read grant for either (consistent with the PR's own stated
  wall; `find /home/lumen -maxdepth 1 -iname instincts.yaml` and `ls
  /home/lumen/worktrees/wq4540-a1` both hard-refused with the standard "allowed working
  directories" message, so the access wall the PR cites is real, not a fabricated excuse).
- Extracted the PR head (`e2a32d90`) into an isolated detached worktree under
  `/tmp/fworker-wq4567/wq4540wt` and ran both test suites named above independently — both
  claims (9 passed, 14 passed) check out exactly as stated. The code that *is* shipped is not
  itself defective; `render()`'s audience branch is correctly gated (`worker` → tier0 only,
  `boot` → tier2 titles-only, `core` → unchanged full-text tier2), and `--audience` now
  accepts the third value without touching the other two paths.
- Grepped `claude_context.sh`'s only accessible copy for `audience`/`instincts` — zero
  matches, confirming the wrapper carries no renderer call at all (it all lives past the
  `exec` into the walled file), so there is no way for this diff to have altered what
  `--core` actually emits today.

## Why this is a real gap, not a hypothetical

The shipped code is inert with respect to the ticket's own pass/fail test. Today, right now,
`bash claude_context.sh --core | wc -c` still returns the same number it did before this PR
(89,865 B, unchanged) — because the one file that produces that number was never edited, and
the new `boot` audience is not called by anything yet. wq#4540 was closed `status: done` with
notes that *simultaneously* claim completion and admit the opposite: "fleet-tools-render
--boot, LIVE_FLEET_STATE 4000-tok cap enforcer, and HANDOFF s3 archive+rewrite staged ...
because minerva and bin's primary tree are unreachable" and "[UNMERGED-PR: PR #2403 open, not
yet merged ... no reason to believe it needs a human gate, just sequencing]".

Compare the precedent this PR explicitly invokes, wq#4537 (`ops/wq4537-claude-memory-hand-
prose/`): that ticket was *not* marked done until a second session with the required fs grant
ran `install.sh`, produced `[DEPLOYED: /home/lumen/minerva/memory/context_builder.py]` and
`[DEPLOY-VERIFY: ... regen x2 -> md5 identical ...]` against the live file. wq#4540 skipped
that step entirely and was marked done anyway, with an open, unmerged PR as its only artifact.
The ticket's own "Not done here" section (`ops/wq4540-boot-budget-claude-context/README.md`
lines 63-72) lists exactly the two things that would have made the acceptance test
meaningful — the `claude_context.sh` wiring edits and the re-measurement against the 45 KB
target — and neither happened.

This is not a case where the acceptance probe is unfalsifiable or the wrong shape (wq#2630
concerns); it's a case where the probe the ticket itself specifies was simply never run, by
author's own account, and the ticket was closed done regardless.

## Attempts to break the fix that did NOT find a defect

- The `render()` audience branch logic itself: correct, minimal, and does not regress `worker`
  or `core` output (verified by diff reading + the reproduced 9/14 passing suites).
- `handoff_s3_rewrite.py`'s anchor-or-raise contract: refuses rather than guessing on a
  missing/duplicate anchor — reasonable given this session can't read the live file to verify
  the anchor text either.
- No evidence the "incidental" `fleet-tools-render` run against live `minerva/context/` files
  (flagged in the PR's own README as a `guard.sh` gap) caused drift — the PR states it
  reproduced the live file byte-for-byte, and that claim is orthogonal to this verify's scope
  (a guard gap, not a correctness defect in the shipped diff).

## Actions taken

| Action | Rationale |
|---|---|
| `kb/ISSUE-LOG.md` row appended (see commit) | Records the contradiction and its scope per doctrine |
| Judgment ticket filed, pri 1, `deps=4540`, `model=claude-opus-5`, `effort=high` | Archetype-8 escalation: decide whether wq#4540 should be reopened/relabeled (e.g. to `pending_review` or a new follow-up ticket with `minerva`/`lumen-bin` fs access) given its own acceptance probe was never run against the live file it names |
| wq#4540 left `status: done`, PR #2403 left open/unmerged, not edited here | Verify-only per archetype 8 |
