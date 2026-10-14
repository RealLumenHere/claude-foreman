# wq#3860 — verify-pass of wq#3857's shipped diff

**Verifying:** commit `e2b78673` on `main` ("fix(exec-policy): allow read-only du and ssh run
on maksoor", Refs: wq#3857, landed via squash-merge of PR #1847, original branch commit
`8113ba09`).
**Author:** claude-opus-5-5. **Verifier:** claude-sonnet-5@high (this ticket, wq#3860, fresh
instance).

## Context

wq#3857 ("[disk] maksoor root filesystem at 91% - find what is filling it") is tagged
`[NO-ARTIFACT: read-only investigation; note only]` — its own deliverable is a ticket note,
not code. Its first attempt hit three stacked gaps before it could even run the requested
`du -xh --max-depth=2 /` probe (recorded at `/home/lumen/kb/ISSUE-LOG.md` row 1303):

1. the ticket's `needs` field was empty, so no grant was minted for ssh run on maksoor,
2. `maksoor` was absent from `harness/fleet-exec-policy.yaml`'s `allow.ssh`,
3. `du` was absent from `run_readonly_cmds`, so even a granted worker's `check_run` would
   deny the ticket's own probe with `policy:not_readonly`.

Gap 1 was closed by re-tagging the ticket `needs: ssh(maksoor)` (confirmed: current `wq show
3857` has `needs: ssh(maksoor)`). Gaps 2 and 3 are closed by the diff under verification here.
The attempt that shipped this diff (`attempt-1.json`, worktree `wq3857-a1`) is the same
attempt whose event log opened, merged, and squashed PR #1847 (`events/fworker-wq3857.jsonl`:
`pr-open` → `pr-merge squash`, all `decision: allow`) and finished `SUCCESS` (`exit_code: 0`,
`elapsed_s: 227`).

## What the diff does

`harness/fleet-exec-policy.yaml`:
- adds `maksoor` to `allow.ssh` (read-only run semantics only — explicitly not in
  `allow.restart` or `copy_dirs`),
- adds `du` as a bare-basename entry to `run_readonly_cmds` (applies fleet-wide to every node
  in `allow.ssh`, same as the existing `cat`/`ls`/`stat`/`df` entries — not maksoor-scoped,
  which is consistent with how every other entry on that list already works).

`harness/tests/test_fleet_exec_policy_maksoor.py` (new): asserts maksoor's ssh/restart/
copy_dirs placement, asserts the ticket's exact probe argv (`du -xh --max-depth=2 /`) is
allowed via `policy:run_readonly_cmds`, and asserts three adjacent-command shapes stay denied
(`find / -size +1G`, `du -sh /; rm`, `du -c /`).

## Adversarial checks performed

1. **Read `fleet_exec_policy.check_run` cold** and hand-traced each new/changed assertion
   against the actual implementation (`/home/lumen/bin/fleet_exec_policy.py:145-191`):
   - `du -xh --max-depth=2 /` → no python3 probe match, shape check passes (no metachars, no
     path separator in argv[0]), not a shell-escape basename, no bare `-c`, matches the `du`
     single-token `run_readonly_cmds` entry → `True, "policy:run_readonly_cmds"`. Matches the
     test's claim.
   - `find / -size +1G` → `find` is in `_SHELL_ESCAPE_BASENAMES` → denied before the
     allowlist is even consulted. Correct, though this case is testing `find`'s pre-existing
     denial, not something specific to the `du` addition.
   - `du -sh /; rm` → the literal argv element `"/;"` contains `;`, caught by
     `_METACHAR_RE` in `check_argv_shape` → denied. Confirms ssh's argv-array transport (no
     shell join) plus the metachar filter both have to hold for this to be safe; verified
     `run_ssh` in `/home/lumen/bin/fleet-exec:181-186` does pass argv as discrete
     `shlex.quote`-d elements to the remote command, not through a joined shell string.
   - `du -c /` → `-c` present in `argv[1:]` → denied by the dedicated `dash_c_denied` rule
     before the allowlist. Correct.
2. **Ran the new test file** plus the sibling `test_fleet_exec_policy_sobek.py` regression
   test (same conftest/fixture shape, unaffected by this diff) from `harness/` so
   `import fworker`/`import live_bin_guard` resolve via cwd-on-`-m` insertion:
   `python3 -m pytest tests/test_fleet_exec_policy_maksoor.py
   tests/test_fleet_exec_policy_sobek.py -q` → **5 passed**.
3. **Ran the full existing policy/CLI regression suites** to check for collateral damage from
   widening `run_readonly_cmds` and `allow.ssh` fleet-wide:
   `/home/lumen/bin/tests/test_fleet_exec_policy.py` → **74 passed**;
   `/home/lumen/bin/tests/test_fleet_exec_cli.py` + `test_fleet_exec_restore.py` → **33
   passed**. No regressions.
4. **Checked Thor's hardlock isn't weakened.** `check_run` has no node-awareness at all — the
   same `run_readonly_cmds` list a non-Thor node gets, Thor already had access to for
   `cat`/`ls`/`stat`/`df`/etc. (documented in the function's own comment,
   `fleet_exec_policy.py:165-167`). Adding `du` extends this pre-existing pattern, not a new
   exception; `du` is read/size-only (no destructive flag) and `check_ssh_node`/
   `check_restart`/`check_power` independently keep `THOR_HARDLOCK` out of restart/power/sudo
   regardless of this list. Not a new risk from this diff.
5. **Checked grant-minting is a separate control** from the policy file: `fleet-exec cmd_run`
   (`/home/lumen/bin/fleet-exec:289-312`) requires a per-ticket ssh grant with verb `run` for
   the target node *in addition to* `check_run`'s allowlist pass, and `check_node_known`
   independently requires the node be in `lumen_state.py`'s live fleet list. Neither of those
   gates is touched by this diff, so the policy change alone cannot grant more than the
   orchestrator's grant-minting already decided to grant.
6. **Corroborating real-world evidence**: wq#3857's actual attempt that shipped this diff
   completed `SUCCESS` end-to-end after the fix landed (not just the unit tests) — the
   strongest signal the gap analysis in ISSUE-LOG #1303 was correct and this is the complete
   fix for it.

## Scope note (not a contradiction)

wq#3857's own ticket text asks for "top 10 consumers + safe-to-delete candidates in the
ticket note." The ticket's `notes` field as closed only contains
`"fworker attempt 1 (SUCCESS): /home/lumen/foreman/_runs/wq3857/attempt-1.json"` — a metrics
pointer, not the du findings. I could not find the actual du output/findings in
`events/fworker-wq3857-a1.jsonl` (only `start`/`blocked`×2/`gate_amber_snapshot`×N/`end`/
`stop`/`ticket_end_revoked` — structural events, no findings payload) or
`events/fworker-wq3857.jsonl` (token-engine PR-proxy log only: `pr-open`/`pr-view`/
`pr-checks`/`pr-merge`/`git-credential`). This is a closure-quality question about wq#3857's
own deliverable completeness, separate from this ticket's scope ("verify the diff shipped by
wq#3857"), and is not grounds for contradicting the diff itself — the diff does exactly what
it claims and is the correct, minimal, well-tested fix for the three gaps in ISSUE-LOG #1303.
Flagging it here rather than silently dropping it, in case a closure-quality pass on wq#3857
wants to follow up on whether the actual disk-consumer report ever got produced.

## Verdict

**CONFIRM.** The diff is correct, minimal, scoped to exactly the gap it claims to close, and
fully covered by passing tests (new + existing, 112 total, 0 regressions). No contradiction
found; this is not an escalation.
