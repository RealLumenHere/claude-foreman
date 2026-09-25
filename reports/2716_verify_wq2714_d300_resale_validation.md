# wq#2716 — verify-pass: wq#2714 D300 GPU resale-validation harness + SOP

**Verdict: CONTRADICT** — the orchestrator's persistent per-card grade log
(`grades/resale_grades.csv`), which the shipped SOP tells operators to read as the
authoritative resale decision, mislabels a `TOOL_MISSING` stage outcome as `FAIL`. This
directly contradicts both the PR's own dry-run claim ("degrades to TOOL_MISSING cleanly")
and the SOP's own documented contract for that exact result
(`docs/wq2714_d300_gpu_resale_sop.md`: "A `TOOL_MISSING` result means the rig is missing a
required binary, not that the card failed"). Everything else in the diff holds up.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1074 (merged as `e65184e`).

## What was verified and holds up

- `bind_check.sh`, `vram_test.sh`, `compute_soak.sh` each correctly distinguish
  `PASS`/`FAIL`/`TOOL_MISSING` in their own `write_result()` JSON output — traced by
  reading each script, not just trusting the commit message.
- `orchestrator.sh` correctly chains the three stages and stops at the first nonzero
  exit (no wasted soak hours on an already-failed card) — matches the documented
  "stop at first FAIL" design.
- `monitor_sysfs.sh`'s background sampling loop and `compute_soak.sh`'s `set +e`/`set -e`
  bracketing around the `clpeak` retry loop are correct: a per-iteration nonzero `clpeak`
  exit does not kill the script early under `set -euo pipefail` (confirmed by reading the
  `set +e ... set -e` fencing at compute_soak.sh:33-50).
- `record_visual_gate.sh` and the CSV schema (`serial,run_ts,stage,result,notes,outdir`)
  are internally consistent with the SOP's "Reading the per-card log" section.
- The claimed dry-run scenario (no card present, tools not installed) does run
  end-to-end without a crash — no bash traceback, no partial writes, no unhandled
  exception. In that narrow sense "degrades ... cleanly" is true.

## What contradicts, with evidence

Reproduced the PR's own claimed scenario directly (worktree
`/home/lumen/worktrees/wq2716-a1`, no GPU/rig, tools absent):

```
$ bash scripts/wq2714_run_resale_validation.sh --serial VERIFYTEST001 --vram-seconds 2 --soak-seconds 2
[...] bind_check -> TOOL_MISSING
[...] HEADLESS GRADE: FAIL at bind_check
HEADLESS_GRADE=FAIL stage=bind_check outdir=.../VERIFYTEST001_20260925T203354Z
(exit 1)
```

The per-stage JSON is correct:

```json
{
  "stage": "bind_check",
  "result": "TOOL_MISSING",
  "detail": "vulkan: vulkaninfo not found | opencl: clinfo ran but reported 0 devices: ..."
}
```

But the row `orchestrator.sh` appends to the append-only grade log hardcodes the literal
string `FAIL`, discarding the `TOOL_MISSING` distinction entirely:

```
serial,run_ts,stage,result,notes,outdir
VERIFYTEST001,20260925T203354Z,bind_check,FAIL,"see .../bind_check.json",...
```

Root cause (`orchestrator.sh:48-67`): each of the three stage gates is

```bash
if ! "$HERE/bind_check.sh" "$OUTDIR" "$EXPECTED_GPU"; then
  echo "$SERIAL,$RUN_TS,bind_check,FAIL,\"see $OUTDIR/bind_check.json\",$OUTDIR" >> "$GRADE_LOG"
  ...
```

i.e. it branches on the stage script's *exit code* and writes the literal string `FAIL`
into the CSV — it never reads the `result` field the stage script itself already computed
and wrote to `<stage>.json`. `bind_check.sh`, `vram_test.sh`, and `compute_soak.sh` all
`exit 1` for *both* `FAIL` and `TOOL_MISSING` (by design, so the orchestrator's chain
stops either way) — so this collapse happens identically in all three stages, not just
`bind_check`. Confirmed by code inspection of all three `write_result(...); ...; exit 1`
sites (`bind_check.sh:43-51`, `vram_test.sh:26-30`, `compute_soak.sh:55-59`).

## Why this is not cosmetic

The SOP's own "Reading the per-card log" section instructs the operator to treat any
`result=FAIL` row in `resale_grades.csv` as scrap-it evidence: *"FAIL grade: any row with
`result=FAIL`. Stop -- resell as parts/scrap, don't advertise as working."* It separately
warns, two sections earlier, that `TOOL_MISSING` must not be read that way. The shipped
orchestrator makes it impossible to follow that second instruction from the CSV alone —
the distinction it depends on is destroyed before the row is written. Concretely: the
very first run on a freshly provisioned rig, before `vulkaninfo`/`clinfo`/
`memtest_vulkan`/`gpu-burn` are installed, permanently logs a `FAIL` row next to that
card's serial number. An operator skimming the CSV (the documented reading path) rather
than opening every stage's JSON detail file would scrap or under-grade a card for a
software-provisioning gap, not a hardware defect — the exact false-negative the SOP text
says it's guarding against.

## Scope note

No automated tests ship with this PR (pure bash, no `test_*.sh`/`test_*.py`); an
unrelated abandoned attempt directory
(`_runs/wq2714/snapshots/...wq2714-a1.../tests/test_sysfs_watch.py`) from a prior,
un-merged branch of this same ticket is not part of what landed and was not evaluated as
part of this PR.

Per verify-pass doctrine (archetype 8), this is a verification, not a re-implementation:
the `orchestrator.sh` FAIL/TOOL_MISSING rollup bug is not fixed under this ticket.
Escalating to opus-5@high per ticket instructions.
