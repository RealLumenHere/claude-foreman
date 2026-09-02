# wq#872 — verify-pass: wq#853 fleet-maint-run G0-G7 rolling executor

**Verdict: CONTRADICT** — the shipped code contradicts its own README's documented
contract for "informational" (no `apply_cmd`) line items. Because stop-on-first-failure
is absolute, a stray `reboot` or `requires_replication_lag_zero` flag left on a no-op
informational line (e.g. an "apt tail — none" row) halts the *entire* unattended
maintenance window, leaving every later, real, mutating item `NOT_ATTEMPTED`. Everything
else in the diff holds up under adversarial testing.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/99 (merged as `7117bcd`,
diffed against merge-base `b103366` to confirm scope: `ops/wq853-fleet-maint-run/{README.md,
fleet_maint_run.py,test_fleet_maint_run.py}`, 998 insertions, no other files touched).

## What was verified and holds up

- `python3 -m pytest ops/wq853-fleet-maint-run/test_fleet_maint_run.py` — 20/20 pass in
  this worktree (`main` checkout, `7117bcd`).
- G0 sha256 gate: `run_batch()` reads the batch file once (`raw = f.read()`), hashes and
  `json.loads()`s the *same* bytes — no TOCTOU window between the hash check and the
  content executed. Mismatch aborts the whole run before any item runs; phase 3b
  (RECORD) still fires (`test_g0_sha256_mismatch_aborts_whole_run_and_still_records`,
  reproduced).
- Ordering law (A11 §5): `NODE_ORDER`/`_sort_key` (`fleet_maint_run.py:61-67,158-166`)
  correctly tiers leaf nodes → service nodes → sobek → maat → lumen, reboot items sorted
  after non-reboot items on the same node.
- Stop-on-first-failure (`fleet_maint_run.py:483-497`): the first item that isn't
  `OK`/`ABSENT` sets `stopped=True`; every later item is recorded `NOT_ATTEMPTED` and its
  command is never invoked.
- Paired-restore registry (P4): `register_paired_restore()` runs before `apply_cmd`,
  `clear_paired_restore()` only after a clean G7 or a successful rollback; a leftover
  registration on disk after an abort is the intended "human/future-supervisor" signal,
  not a bug.
- `dispatcher_stop_cmd`/`dispatcher_restore_cmd` pairing: restore fires in phase 3b
  whenever stop was actually attempted, including when a later item fails; no restore
  call fires when G0 fails before stop ever ran (`test_dispatcher_restore_runs_even_on_g0_failure`,
  reproduced).
- `validate_batch()` correctly refuses to run rather than guess: unknown node, missing
  `rollback_cmd`/`rollback_dryrun_cmd`/`pre_state_cmd` on an apply item, missing
  `health_probe_cmd`, `requires_backup` without `backup_precondition_cmd` — all abort
  before any mutation, all reproduced.

## Contradiction — informational (no `apply_cmd`) items are not gate-free

README, "Batch schema": *"An item with no `apply_cmd` is a pure informational line ...
and runs no gates."* `fleet_maint_run.py:219-247` (`run_item`) contradicts this: the
`reboot`/consent check (line 229) and the `requires_replication_lag_zero` check
(line 235) both run *before* the `if not apply_cmd: return OK` short-circuit (line 244).

First-hand reproduction (`/tmp/adv_test2.py`, real `run_batch()`, real sha256, no mocking
beyond `sleep_fn`/G5 timing — same as the shipped tests):

```
item1 = {"id": 1, "node": "gameboy", "component": "apt-tail",
         "reboot": True, "consent": False}   # informational: no apply_cmd
item2 = {"id": 2, "node": "apep", "component": "real-comp", "apply_cmd": "true", ...}
```

Result: `status=STOPPED`. Item 1 → `ABORTED`/`gate_failed=CONSENT`,
`"reboot item lacks live per-node consent — fail closed (A11 §7 item 10)"`. Item 2 (a
fully valid, real mutating item on a different node) → `NOT_ATTEMPTED`,
`"run stopped earlier — stop-on-first-failure is absolute (A11 §4)"`. `apply_cmd: "true"`
for item 2 was never invoked.

This is exactly the failure mode the design says it exists to avoid: an unattended
maintenance window (launched via `lumen-run` specifically so "a Commander flap can't
kill it mid-node") stops early on a bug in the runner's own input handling, not on a real
fleet condition. The shipped test suite's own informational-item test
(`test_item_with_no_apply_cmd_needs_no_gates`) only exercises a bare informational item
with neither flag set, so it doesn't catch this.

Whether `reboot`/`requires_replication_lag_zero` would ever realistically end up set on
an informational line depends on the not-yet-built phase-1 assembler (or, "for the first
window," a human editing the batch JSON by hand per the README) — but the README treats
informational lines as a first-class, expected case, not a hypothetical, and nothing in
the shipped validation or gate code defends the invariant it claims to guarantee.

## Secondary observation (not gate-blocking, noted for the fix)

`ItemResult.gate_failed` (`fleet_maint_run.py:205-216`) is only ever set on the
preflight-style aborts (CONSENT, REPL_LAG, G1, G2, G3). The `rollback()` closure
(line 297-313), used for apply failure, G5, and G6 failures, never sets it — those rows
render `gate` as `-` in the phase-3b report table even though `detail` names the gate
(e.g. "G5 health probe failed..."). Confirmed by reading `rollback()` and
`render_report()`; `stop_reason` at line 497 already works around this with
`r.gate_failed or 'apply/health'`, suggesting the gap was known but not closed.

## Disposition

Ship-quality work on the gate sequence, ordering law, paired-restore bookkeeping, and
phase-3b RECORD guarantees for actual apply items — all reproduced and hold up. The
informational-item gap is real, reproducible against the merged code with no mocking
beyond what the shipped tests already use, and has a concrete unattended-run failure
mode (early abort on a no-op line, real work left `NOT_ATTEMPTED`). Escalating to
opus-5@high per this ticket's own instructions (wq#853 [judgment] ticket) rather than
re-implementing — verify-pass is not a re-implementation gate.
