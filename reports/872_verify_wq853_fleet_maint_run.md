# wq#872 — verify-pass: wq#853 fleet-maint-run G0-G7 rolling executor

**Verdict: CONTRADICT** — the shipped code contradicts its own README's documented
contract for "informational" (no `apply_cmd`) line items, and the contradiction is not
merely cosmetic: because stop-on-first-failure is absolute, a stray `reboot` or
`requires_replication_lag_zero` flag left on a no-op informational line (e.g. an
"apt tail — none" row) halts the *entire* unattended maintenance window, leaving every
later, real, mutating item `NOT_ATTEMPTED`. Everything else in the diff holds up under
adversarial testing.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/99 (merged as `7117bcd`,
content-identical to the PR branch head `a2f56e3` — diffed to confirm before trusting
either).

## What was verified and holds up

- `python3 -m pytest ops/wq853-fleet-maint-run/test_fleet_maint_run.py` — 20/20 pass,
  reproduced against the merged `main` copy of the file, not just the PR branch.
- G0 sha256 gate: reads the batch file once (`raw = f.read()`), hashes and
  `json.loads()`s the *same* bytes — no TOCTOU window between the hash check and the
  content actually executed. Mismatch aborts the whole run before any item runs, and
  phase 3b (RECORD) still fires. Confirmed by reading `run_batch()` and the shipped
  `test_g0_sha256_mismatch_aborts_whole_run_and_still_records`.
- Ordering law (A11 §5): `NODE_ORDER`/`_sort_key` correctly tiers leaf nodes → service
  nodes → sobek → maat → lumen, with reboot items sorted after non-reboot items on the
  same node. Matches the shipped ordering tests; traced by hand against the table in the
  README and found no discrepancy.
- Stop-on-first-failure: the first item that isn't `OK`/`ABSENT` sets `stopped=True`,
  every later item is recorded `NOT_ATTEMPTED` and its command is never invoked (`run_cmd`
  isn't called at all for those items — verified by reading the loop in `run_batch`, not
  just trusting the test).
- Paired-restore registry (P4): `register_paired_restore()` runs before `apply_cmd`,
  `clear_paired_restore()` only after a clean G7 or a successful rollback; a leftover
  registration on disk after an abort is the intended "human/future-supervisor" signal,
  not a bug.
- `dispatcher_stop_cmd`/`dispatcher_restore_cmd` pairing: restore fires in phase 3b
  whenever stop was actually attempted, including when a later item fails or G0 fails
  before stop ever ran (no restore call in that case). Confirmed both shipped tests plus
  read of `_finish`/`run_batch`.
- Batch schema validation (`validate_batch`) correctly refuses to run rather than guess:
  unknown node, missing `rollback_cmd`/`rollback_dryrun_cmd`/`pre_state_cmd` on an
  apply item, missing `health_probe_cmd`, `requires_backup` without
  `backup_precondition_cmd` — all abort before any mutation, all reproduced.

## Contradiction — informational (no `apply_cmd`) items are not gate-free

README, "Batch schema": *"An item with no `apply_cmd` is a pure informational line (e.g.
the assembler's 'apt tail — none' rows) and runs no gates."* This is false as shipped.

`run_item()` (`ops/wq853-fleet-maint-run/fleet_maint_run.py:219-247`) checks the reboot/
consent gate and the `requires_replication_lag_zero` gate **before** it checks whether
`apply_cmd` is even set:

```python
is_reboot = bool(item.get("reboot"))
if is_reboot and not item.get("consent"):
    r.status = "ABORTED"; r.gate_failed = "CONSENT"; ...
    return r

if item.get("requires_replication_lag_zero"):
    ok, detail = _run_check(item.get("replication_lag_cmd"), run_cmd)
    if not ok:
        r.status = "ABORTED"; r.gate_failed = "REPL_LAG"; ...
        return r

apply_cmd = item.get("apply_cmd")
if not apply_cmd:
    r.status = "OK"; r.detail = "no apply_cmd — informational line item, no gates run"
    return r
```

`validate_batch()` doesn't catch this either — it does `if not it.get("apply_cmd"):
continue` before ever looking at `reboot`/`requires_replication_lag_zero`, so a batch
with these fields set on an informational line passes validation cleanly. Nothing in the
shipped code enforces the invariant the README promises.

Reproduced directly against the merged `fleet_maint_run.py` (`run_item()`, no mocking):

```
item = {"id": 1, "node": "gameboy", "component": "apt-tail", "reboot": True, "consent": False}
-> status=ABORTED gate_failed=CONSENT

item = {"id": 2, "node": "maat", "component": "apt-tail",
        "requires_replication_lag_zero": True, "replication_lag_cmd": "false"}
-> status=ABORTED gate_failed=REPL_LAG
```

Both should be `status=OK` per the README's own contract for a no-`apply_cmd` item. End
-to-end confirmation through `run_batch()` (real batch file, real sha256, no mocks
except `sleep_fn`/G5 timing) shows the operational blast radius: an informational
`apt-tail` line on `gameboy` with a leftover `requires_replication_lag_zero: true` flag
(e.g. copy-pasted from a template item and not stripped) aborts at that line and the run
status is `STOPPED`; a second, real, fully-valid mutating item on `apep` right after it
is recorded `NOT_ATTEMPTED` and its `apply_cmd` never runs — despite item 1 never being
a mutation at all.

This is exactly the failure mode the design says it's trying to avoid: an unattended
maintenance window (launched via `lumen-run` specifically so "a Commander flap can't
kill it mid-node") stopping early on a bug in the runner's own input handling, not on a
real fleet condition. The shipped test suite's own informational-item test
(`test_item_with_no_apply_cmd_needs_no_gates`) only exercises a bare informational item
with neither flag set, so it doesn't catch this.

Whether `reboot`/`requires_replication_lag_zero` would ever realistically end up set on
an informational line depends on the not-yet-built phase-1 assembler (or, "for the first
window," a human editing the batch JSON by hand per the README) — but the README treats
informational lines as a first-class, expected case, not a hypothetical, and nothing in
the shipped validation or gate code defends the invariant it claims to guarantee.

## Disposition

Ship-quality work on the gate sequence, ordering law, paired-restore bookkeeping, and
phase-3b RECORD guarantees for actual apply items — all reproduced and hold up. The
informational-item gap is real, reproducible against the merged code with no mocking
beyond what the shipped tests already use, and has a concrete unattended-run failure
mode (early abort on a no-op line, real work left `NOT_ATTEMPTED`). Recommend `run_item`
check `apply_cmd` presence first and return the informational-OK short-circuit before
the consent/replication-lag gates, plus a `validate_batch` check that flags
`reboot`/`requires_replication_lag_zero` set without `apply_cmd` as a batch problem
(mirrors the existing `requires_backup`-without-`backup_precondition_cmd` check already
in that function) so a malformed batch is caught at G0/validation time instead of mid
-run. Filing wq#853's own author's shape of fix is a judgment call, not mine to make
here — escalating to opus-5@high per this ticket's own instructions.
