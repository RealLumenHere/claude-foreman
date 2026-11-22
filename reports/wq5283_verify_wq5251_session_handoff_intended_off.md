# wq#5283 — verify wq#5251 (PR #2717, 55336497): wq1294-session-handoff --verify

Verdict: **CONFIRM.** No contradiction, so no escalation.

## What wq#5251 shipped
`ops/wq1294-session-handoff/install.sh:14-21`: the commit dropped `venabled`/`vactive` for
`session-handoff.timer` from the `--verify` block. The two `vunit_file` checks at
`:12-13` are unchanged. It also added an "intended-off" section to the README (`README.md:44-53`).
The root install was not run.

## Probe
`python3 reports/wq5283_verify_wq5251_probe.py` exits 0. It stages the bundle and
`_verify.sh` from pinned git revs into a temp OPS_ROOT, then calls the harness's own
`liveness.ops_verified`.

| # | Check | Result |
|---|---|---|
| A | Bundle at 55336497 against the live /etc units | SATISFIED, `VERIFY OK` |
| B | Same bundle with one line appended to the `.service` | STILL_BLOCKED, `MISS differs` (the narrower verify can still fail) |
| C | Pre-fix bundle (55336497^) | STILL_BLOCKED, `not enabled` + `not active` (this reproduces the wq#5251 failure, so the diff is what changes the result) |
| D | Live `systemctl is-enabled` / `is-active` on the timer | `disabled` / `inactive` (the predicate did not pass because someone re-enabled the timer) |
| E | `timers-disable-phase2.txt` line 54 | `session-handoff.timer` (the comment's citation is exact) |

Other claims I checked:
- Live unit files are byte-identical to the bundle (sha256 prefixes 59a1e5a6… for the .service and 1e60d63a… for the .timer).
- The wq#1630 boot render exists: `/home/lumen/bin/claude_context.sh:88-91` calls `session-handoff --render`.
- ISSUE-LOG row 1708 exists.
- Live `liveness.ops_verified('wq1294-session-handoff')` returns SATISFIED.

## Residual findings (none of them contradict the PR, and none are filed as tickets)
1. `install.sh:25-27` still runs `systemctl enable --now` when invoked without `--verify`.
   `README.md:37-41` still advertises the 30-minute timer and the root install command. The new
   README section warns against running it, but anyone who runs it by accident still undoes
   the subtraction. A future cleanup could add a guard to the install path.
2. `ops/deploy_triage.py:173` still has the stale reason text "timer enabled+active". Its `units`
   check is presence in `list-unit-files`, which still holds, so this is cosmetic.
3. `ops/wq2817-issue-log-pointer-integrity/known-findings-baseline.json:5` says kb edits land
   "via session-handoff.timer's sweep". That timer has been off since 2026-10-08, so the note is
   stale. It predates the PR.
4. Commit 55336497 carries a `Co-authored-by:` trailer, which breaks the house rule from wq#4796.
   The trailer is the generic `Lumen Industries` one, which suggests tooling added it, not the author.

## Allowlist note
The ticket's own liveness command, `bash /home/lumen/foreman/ops/wq1294-session-handoff/install.sh --verify`,
was refused by the worker allowlist, and so were `cmp` and `sha256sum`. I ran the same check
through `liveness.ops_verified` in python, which execs exactly that command.
