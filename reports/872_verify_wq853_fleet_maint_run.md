# wq#872 — verify-pass: wq#853 fleet-maint-run G0-G7 rolling executor

**Verdict: CONTRADICT** — the shipped code contradicts its own README's documented
contract for "informational" (no `apply_cmd`) line items. Because stop-on-first-failure
is absolute, a stray `requires_replication_lag_zero` or `reboot` flag left on a no-op
informational line (e.g. an "apt tail — none" row) halts the *entire* unattended
maintenance window on a line that mutates nothing, leaving every later, real, mutating
item `NOT_ATTEMPTED`. Everything else in the diff holds up under adversarial testing.

PR under review: http://100.121.17.5:3000/lumen/claude-foreman/pulls/99, merged as
`7117bcd` (content-identical to PR head `a2f56e3`, confirmed via
`git diff a2f56e3 HEAD -- ops/wq853-fleet-maint-run/` = empty). Scope confirmed via
`git diff b103366 a2f56e3 --stat`: `ops/wq853-fleet-maint-run/{README.md,
fleet_maint_run.py,test_fleet_maint_run.py}`, 998 insertions, no other files touched.

## What was verified and holds up

- `python3 -m pytest ops/wq853-fleet-maint-run/test_fleet_maint_run.py` — 20/20 pass,
  run directly against the merged file on `main` (HEAD `44160ef`, wq853 landed at
  `7117bcd`).
- G0 sha256 gate (`fleet_maint_run.py:439-450`): `run_batch()` reads the batch file
  once, hashes and `json.loads()`s the same bytes — no TOCTOU window. Mismatch aborts
  the whole run before any item runs; phase 3b (RECORD) still fires
  (`test_g0_sha256_mismatch_aborts_whole_run_and_still_records`, reproduced by direct
  test run).
- Ordering law (A11 §5): `NODE_ORDER`/`_sort_key` (`fleet_maint_run.py:61-67,158-166`)
  correctly tiers leaf nodes → service nodes → sobek → maat → lumen; reboot items sort
  after non-reboot items on the same node (`test_ordering_law_...`,
  `test_reboot_runs_after_package_items_on_same_node` pass).
- Stop-on-first-failure (`fleet_maint_run.py:483-497`): the first item that isn't
  `OK`/`ABSENT` sets `stopped=True`; every later item is recorded `NOT_ATTEMPTED`, its
  command never invoked.
- Paired-restore registry (P4, `fleet_maint_run.py:138-153,294-295,309,358`):
  `register_paired_restore()` runs before `apply_cmd`; `clear_paired_restore()` fires
  only after a clean G7 or a successful rollback. Confirmed via the shipped
  `test_paired_restore_registered_before_apply_and_cleared_after_g7`, which spies on
  `run_cmd` to read the registry file mid-apply.
- `validate_batch()` (`fleet_maint_run.py:174-200`) correctly refuses to run rather than
  guess: unknown node, missing `rollback_cmd`/`rollback_dryrun_cmd`/`pre_state_cmd` on
  an apply item, missing `health_probe_cmd`, `requires_backup` without
  `backup_precondition_cmd` — all abort before any mutation.
- Phase 3b RECORD (`fleet_maint_run.py:379-422`): dispatcher restore, state-regen,
  episode commit, report file, ntfy, and `wq done` all fire even on a G0 abort with zero
  items touched — confirmed by `test_dispatcher_restore_runs_even_on_g0_failure` and
  `test_phase_3b_closes_wq_ticket_with_report_path`.

## Contradiction — informational (no `apply_cmd`) items are not actually gate-free

README ("Batch schema", `README.md:66-67`): *"An item with no `apply_cmd` is a pure
informational line item (e.g. the assembler's 'apt tail — none' rows) and runs no
gates."* `fleet_maint_run.py:219-247` (`run_item`) contradicts this directly: the
`reboot`/consent check (line 229) and the `requires_replication_lag_zero` check
(line 235) both execute *before* the `if not apply_cmd: return OK` short-circuit
(line 244) — so either flag, if present on an informational row, gates the row anyway.

First-hand reproduction against the merged file
(`ops/wq853-fleet-maint-run/fleet_maint_run.py` at `main` `44160ef`), real `run_batch()`,
real sha256, no mocking beyond `sleep_fn`/`g5_t1`/`g5_t2` (same allowance the shipped
tests use):

```python
item = {"id": 1, "node": "maat", "component": "apt-tail", "lane": "APT",
        "requires_replication_lag_zero": True, "replication_lag_cmd": "false"}
# no apply_cmd -- per README this is a pure informational line, "runs no gates"
```

Result:

```json
{
  "status": "STOPPED",
  "items": [{"id": 1, "node": "maat", "component": "apt-tail",
             "status": "ABORTED", "gate_failed": "REPL_LAG",
             "detail": "pg_stat_replication lag check failed: "}]
}
```

The run stops on a line that was never supposed to run any gate at all. On a real batch
this halts every later item in `NODE_ORDER` (here, only `lumen` follows `maat`, but
nothing in the code confines the flag to `maat`-tier items — it is read unconditionally
off any item).

The same ordering flaw applies to `reboot`/`consent` (`fleet_maint_run.py:228-233`,
which also runs before the `apply_cmd` check): an informational item with `reboot: true`
and no live `consent` would abort as `CONSENT`-failed even though it has no `apply_cmd`
and therefore nothing to reboot. Verified by code inspection of the same
line-229-before-line-244 ordering (not independently re-executed this session — the
`requires_replication_lag_zero` repro above already demonstrates the identical root
cause: any per-item precondition check placed above the `apply_cmd` guard fires
regardless of whether the item is informational).

The shipped test suite's own informational-item test
(`test_item_with_no_apply_cmd_needs_no_gates`) only exercises a bare informational item
with neither flag set, so it doesn't catch this gap. `validate_batch()` also doesn't
catch it: for items with no `apply_cmd`, validation returns at line 189-190 before ever
looking at `requires_replication_lag_zero`/`replication_lag_cmd`/`reboot`/`consent`.

Whether an informational line would ever realistically carry `requires_replication_lag_zero`
or `reboot` depends on the not-yet-built phase-1 assembler (or, "for the first window," a
human editing the batch JSON by hand, per the README's own admission) — but the README
treats informational lines as a first-class, expected case (they're explicitly named
after the real "apt tail — none" assembler output), not a hypothetical, and nothing in
the shipped validation or gate code defends the invariant the README claims to
guarantee.

## Secondary observation (not gate-blocking, noted for the fix)

`ItemResult.gate_failed` (`fleet_maint_run.py:205-216`) is only ever set on the
preflight-style aborts (CONSENT, REPL_LAG, G1, G2, G3). The `rollback()` closure
(`fleet_maint_run.py:297-313`), used for apply failure, G5, and G6 failures, never sets
it — those rows render `gate` as `-` in the phase-3b report table even though `detail`
already names the gate (e.g. "G5 health probe failed..."). `stop_reason` at line 497
already works around this with `r.gate_failed or 'apply/health'`, suggesting the gap was
noticed but not closed in the shipped diff.

## Disposition

Ship-quality work on the gate sequence, ordering law, paired-restore bookkeeping, and
phase-3b RECORD guarantees for actual apply items — all reproduced and hold up. The
informational-item gap is real, reproduced against the merged code with no mocking
beyond what the shipped tests already use, and has a concrete unattended-run failure
mode: an unattended maintenance window (launched via `lumen-run` specifically so "a
Commander flap can't kill it mid-node") can stop early on a bug in the runner's own
input handling rather than a real fleet condition. Escalating to opus-5@high per this
ticket's own instructions rather than re-implementing — verify-pass is not a
re-implementation gate.
