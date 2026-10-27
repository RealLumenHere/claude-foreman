# wq#4706 — judgment on wq#4657's contradiction of wq#4654

2026-10-27. wq#4657 verify-passed wq#4654 and contradicted its done claim on two counts.
Both are upheld. Both are fixed.

## Count 1 — the PR was unmerged. UPHELD, fixed.

wq#4654 closed 2026-10-26 12:28 PDT citing PR #2473 (`claude-foreman`, head `938dabc02b`)
while that PR was open. Confirmed independently here: before this ticket,
`git grep prune_vault origin/main -- harness/action_gate.py` matched nothing.

Review pass before landing, as the ticket asked:

- The diff is 3 files, +123/-1: `harness/action_gate.py` (`SNAP_PRUNE_DAYS`,
  `_carries_secret_pattern`, `prune_vault`, a `prune-vault` argv branch),
  `harness/tests/conftest.py` (`_gate_snap_vault_in_tmp`, the autouse fixture that stops
  tests writing into the real vault — the thing that grew it to 2440 files), and 50 lines of
  tests.
- `time` and `read_dir_sweep as _rds` are both imported at `harness/action_gate.py:32,36`, so
  `prune_vault` and `_carries_secret_pattern` have what they reference; `read_dir_sweep.py`
  is present in `harness/`, which is what `python3 harness/action_gate.py` puts on `sys.path`.
  Were the import to fail, `_carries_secret_pattern` returns `True` and the pass retires
  nothing — fail-closed, which is the correct direction.
- `pytest harness/tests/test_action_gate.py` at `938dabc0`: **98 passed**.

Landed: `lumen-pr land /home/lumen/foreman 2473 --merge` → merged as `938dabc02b`, primary
tree fast-forwarded to `2f2c617a`, remote branch deleted.

The first live run retired the backlog wq#4657 measured: 2440 files in
`~/.secrets/gate-snapshots`, 1093 past the 30-day window, 517 of those still carrying a
secret pattern and therefore left for `secret-backup-sweep`, **576 pruned**. A second pass
now reports `would prune 0 file(s)` — the backlog is drained, not merely scheduled.

## Count 2 — the code had no caller. UPHELD, fixed.

Confirmed at the PR head: `prune-vault` / `prune_vault` matched only
`harness/action_gate.py` and its tests. No timer, no cron, no sweep. Merged as it stood, the
PR would not have retired a single file on its own.

Fixed by `ops/wq4706-gate-snapshot-prune-timer/` — a `gate-snapshot-prune` service+timer,
daily at 05:10, running `python3 /home/lumen/foreman/harness/action_gate.py prune-vault` as
`lumen` on the primary tree, with `install.sh --verify` per `ops/VERIFY_CONTRACT.md`.

The owner decision and the three reasons for a separate unit rather than a call folded into
`secret-backup-sweep` are recorded in that bundle's `README.md`: the scheduler belongs in the
repo that holds the code (a cross-repo caller breaks with neither repo's tests watching, and
`ops-deploy-drift` / the wq#2943 gate only watch `claude-foreman/ops/*`);
`secret-backup-sweep.service` has already overloaded its exit codes with
`SuccessExitStatus=1` for the ORPHAN_LIVE finding, so a second job under that unit either
disappears into the success set or pages as a credential orphan; and the two jobs prove
different things — a value-level dead-check versus a blunt age cutoff for the `NO_SECRETS`
class the sweep's `cmd_apply` structurally never claims.

This ticket also added `prune-vault --dry-run` (`harness/action_gate.py`, same selection, no
`unlink`) so the bundle has a read-only probe against the real vault for both its
install-time pre-check and its `--verify`. Without it the only way to prove the `ExecStart`
works is to delete files, which is not a pre-flight check. `--verify` asserts the summary
line names `/home/lumen/.secrets/gate-snapshots`, passed explicitly rather than left to
`expanduser("~")`: `--verify` runs as root as often as as `lumen`, and under root the default
resolves to the absent `/root/.secrets`, where `prune_vault` returns `[]` and the probe could
not fail (wq#2630). Tests: **100 passed**, the two new ones covering the dry-run at both the
function and the CLI boundary.

`prune_vault` was NOT widened: a pre-image carrying a secret pattern is kept at any age, and
`_carries_secret_pattern` fails closed on unreadable, binary, or a missing `read_dir_sweep`.

## Count 3 — the done gate. NOT a gate hole. The debt is uncollected.

The ticket asked why the `[UNMERGED-PR]` tag is advisory rather than a refusal. It is not
advisory, and the gate is not open:

`/home/lumen/bin/wq:3252-3266` — when the cited artifact is a PR URL, `wq done` asks the
forge for its merge state and **refuses with exit 3** if the verdict is `open`. The refusal
text is explicit that a PR URL proves a branch exists, not that the work is on main. The
only way past it is `--unmerged "<reason>"` (wq#899), an explicit on-the-record override,
and the `[UNMERGED-PR: ...]` stamp at `wq:3267-3269` is written *because* that override was
used. wq#4654 did not slip through a hole; an agent chose the override and said so.
`research/TICKET_CLOSURE_AUDIT.md:72` classifies this case the same way — "closed on
purpose, on the record (Gitea; the gate works there)" — and separates it from the 13
false-completes at `:71`, which were the *real* hole (F1: the gate failed open on github.com
because `_forge_token()` read a stripped env var, so those tickets carry no tag at all).

So converting the stamp into a refusal would be the wrong fix. It would remove a deliberate
escape hatch that is sometimes right, and it would not have caught wq#4654's second and worse
defect — the missing caller — which `--unmerged` has nothing to do with.

What is actually missing is the collection step. `[UNMERGED-PR]` is written and then nobody
reads it: the stamp has no consumer anywhere in `bin/` or `foreman/` outside its own test and
two retrospective reports. wq#899's reconciliation (`ops/wq899-pr-recon/pr_recon.py`) was a
one-shot — it has no timer and no service, so the ~40-PR backlog it cleared on 2026-08-23 is
free to re-accumulate, and did. The override records a debt and nothing comes to collect it;
PR #2473 sat open for a day and was found by a verify ticket rather than by the gate that
stamped it.

Filed as its own ticket rather than fixed here. `wq` is the tool every concurrent fleet
worker is calling right now, in a different repo from this worktree, and the fix is a design
change to override semantics (auto-file a landing follow-up on `--unmerged`, mirroring the
`[ROOT-APPLY-FILED]` pattern already at `wq:3236-3243`, or give `pr_recon.py` the timer it
never got) — not a one-line patch a build worker should land mid-flight behind an unrelated
ticket's acceptance gate.
