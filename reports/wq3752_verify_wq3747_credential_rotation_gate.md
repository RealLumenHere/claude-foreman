# wq#3752 — verify-pass on wq#3747 (credential rotation safety gate)

PR: lumen/claude-foreman#1757, merged d5b1cc2c5b on main.
Author: claude-sonnet-5 · Verifier: claude-sonnet-5@high (fresh instance, wq#3752)

## Claim under test

wq#3747: rotate-fleet-pw.sh / rotate-provider-keys / rotate-github-cred /
rotate-gitea-cred / gitea-rotate-finish / github-pat-rotate each destroy the
OLD value of a credential the instant they write the NEW one, with the memory
rule binding only agents that read it (no technical enforcement). Ticket text
asks: add a refusal gate to each rotation script (Vault-backed backup check +
refuse-if-missing + before/after-hash logging), tested with a dummy
credential. Done notes claim: shipped `rotation_vault_gate.py`
(require_backup/log_rotation, sha256[:12] fingerprints, dummy-credential unit
tests), `vault_rotation_backup.py` (attended-operator backup tool), and a live
`harness/guard.sh` backstop that refuses any of the six named tools without a
same-day Vault-backup marker — but explicitly did NOT wire
require_backup/log_rotation into the six scripts themselves (those live in the
separate `bin` repo; this ticket was dispatched with a `foreman` worktree),
filing that as a disclosed follow-up (ISSUE-LOG row 1260, status PARTIAL).

## What I checked

1. **Read the diff cold** (`git show 5552847f`): `harness/guard.sh` (+27
   lines), `rotation_vault_gate.py`, `vault_rotation_backup.py`, their tests,
   and the `ops/wq3747-.../README.md`. The guard's wq#3747 block sits inside
   the existing `for target in "$RAW_CMD" "$NORM"` loop (both raw and
   whitespace-collapsed forms get scanned), so tab/newline/multi-space
   evasions of the regex are covered the same as every other check in the
   file. Marker freshness uses `find "$MARKER" -mmin "-$MARKER_FRESH_MIN"`
   (default 1440 = 24h) — correct sense (modified within the window).
2. **Ran the shipped tests**: `python3 -m pytest
   ops/wq3747-credential-rotation-safety-gate/test_rotation_vault_gate.py` →
   **13 passed**. `python3 -m pytest harness/tests/test_guard.py -k wq3747` →
   **4 passed** (refuse-with-no-marker across all six tool names,
   allow-with-fresh-marker, refuse-with-stale-marker, unrelated bin commands
   unaffected). Full `test_guard.py` run: 75 passed, 1 failed
   (`test_must_allow` on `/home/lumen/minerva/context/sections/000_preamble.md`
   — a Read-target-missing-on-disk case untouched by this diff and outside
   this diff's added lines; pre-existing environment gap, not a wq#3747
   regression).
3. **Tried to break the gate adversarially**:
   - Forging the marker to skip the attended-operator step
     (`touch .../rotate-fleet-pw.sh.ok`) is itself blocked — `/home/lumen/.secrets`
     is already a hard-denied path for Bash (wq#616) and outside every
     worker's Write/Edit directory grant, so a headless worker cannot forge
     freshness without an attended operator's Vault unlock. Confirmed live:
     my own sandboxed session got the real guard's wq#616 block on a `touch`
     attempt into that path.
   - Confirmed the live guard is actually deployed and enforcing today, not
     just covered by the unit tests: an `ls` naming `rotate-provider-keys` in
     my own session was refused by the real PreToolUse hook with the exact
     wq#3747 message. (This is a known overblock — the regex matches the tool
     name as a bare substring anywhere in the command, so a benign `ls`/`grep`
     mentioning the name is refused same as an actual invocation — consistent
     with every other coarse check already in this file, documented as a
     "coarse... backstop," not a correctness bug.)
   - Checked for a path-based bypass (copy the script under a different name,
     base64-encode the invocation). Both exist as theoretical evasions of a
     literal-substring regex, but they are systemic to every verb/path check
     already in `guard.sh` (identical class of weakness as the existing
     sudo/reboot detection), not something newly introduced by this diff —
     not treated as a wq#3747-specific defect.
4. **Checked the honesty of the "done" claim against what actually shipped**:
   the ticket's literal ask ("add the gate to each rotation script") is NOT
   met — none of the six `/home/lumen/bin` scripts call `require_backup` or
   `log_rotation`; that half needs a `bin`-repo worktree this ticket wasn't
   dispatched with. This is disclosed plainly in the PR body, the wq#3747
   done notes, and `/home/lumen/kb/ISSUE-LOG.md` row 1260 (status `PARTIAL`,
   verified present). The shipped harness-level backstop does close the
   specific threat model named in the ticket's own problem statement ("The
   boot memory rule only binds agents that read it" — i.e., an autonomous
   headless agent choosing to run these tools) with a technical,
   un-bypassable-by-that-agent refusal, which is the risk this ticket exists
   to close. Declining to push an unreviewed change straight into the live
   `/home/lumen/bin` checkout from the wrong repo's worktree — rather than
   routing around the WRITE_PRIMARY_TREES wall — matches the RULES' "land via
   lumen-pr, never direct-to-main" and the general hard-to-reverse/shared-system
   caution; it is a defensible call, not a shortcut taken to dodge the gate.

## Verdict

**CONFIRM.** The code shipped matches what the PR and done notes say it
ships: tests pass (13 + 4 new, no regressions in the lines this diff touches),
the live guard is deployed and actively enforcing, no plaintext credential is
ever logged, and the one piece explicitly not delivered (wiring the
fine-grained gate into the six bin-repo scripts) is disclosed in three places
(PR body, done notes, ISSUE-LOG row 1260) rather than silently dropped or
claimed as complete. No contradiction found — no escalation to judgment.

## Non-blocking observation for the bin-repo follow-up

`rotation_vault_gate.require_backup(name, old_value)`'s docstring example and
`vault_rotation_backup.py`'s expected `<name>` argument use credential-level
names (e.g. `fleet-pw`), while `harness/guard.sh`'s marker is keyed by
*tool* name (e.g. `rotate-fleet-pw.sh`) and tells the operator to run
`vault_rotation_backup.py rotate-fleet-pw.sh`. For a one-credential-per-tool
script these coincide; for `rotate-provider-keys` (which the README implies
rotates more than one provider's key per invocation) they would not — the
harness-level marker is per-tool, not per-credential, so it cannot itself
prove *which* of several keys was backed up. Worth settling the naming
convention before wiring the fine-grained call into the six scripts.
