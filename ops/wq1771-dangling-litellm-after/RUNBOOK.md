# wq#1771 — drop dangling After=litellm.service (2 units wq#1659 missed)

Source: wq#1765 verify pass ran the shipped `dangling_target_lint()`
(staged in `ops/wq1659-dangling-service-afters/staged/unit-drift`) against
the live fleet and found 2 units outside wq#1659's scope with the same
dangling `After=litellm.service` pattern FLEET_SERVICE_ATLAS.md #13 flagged
for the other 5:

- `/etc/systemd/system/lumen-brain-health.service`
  (`After=network.target litellm.service`)
- `/etc/systemd/system/lumen-predicate-audit.service`
  (`After=network-online.target litellm.service`)

## Why this ships as a diff, not a live edit

Same constraint as wq#1659: the live unit files are under
`/etc/systemd/system`, which is outside this worker's sandboxed
worktree/`--add-dir` set (confirmed: `test -w` on the file fails). This
ticket read the live units via `systemctl cat` to confirm exact content,
but cannot write them directly.

## Why no ExecStartPre wait-for-port

Unlike the 5 fleet-researcher/convo-learn/research-index units in wq#1659,
neither of these two is an inference client gated on litellm's readiness —
`lumen-brain-health` is a health watchdog and `lumen-predicate-audit` is a
grounding audit, so there's nothing to wait on port 4000 for. Dropping the
dangling token is sufficient.

## Apply

1. `systemctl cat lumen-brain-health lumen-predicate-audit` first to
   reconfirm current content (units may have drifted since this ticket).
2. Apply the corresponding hunk in `units.diff` to each unit file.
3. `systemctl daemon-reload`
4. Verify:

       systemctl cat lumen-brain-health lumen-predicate-audit | grep -E '^After='

   Neither line should contain `litellm.service`.

## Fleet-wide confirmation

Once applied, `unit-drift`'s `dangling_target_lint()` (from wq#1659) should
report 0 dangling targets fleet-wide, not just for the original 5 — closing
out FLEET_SERVICE_ATLAS.md #13 completely.
