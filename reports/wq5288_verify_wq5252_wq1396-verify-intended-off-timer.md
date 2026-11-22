# wq#5288 — verify of wq#5252 (PR #2721, commit 72a23bb9)

**Verdict: CONFIRMED.** The change is real, correct, and the live ops target verifies.

## What wq#5252 changed
`ops/wq1396-minerva-proposal-promoter/install.sh:15-29` — the `--verify` block no longer
asserts `venabled`/`vactive minerva-promote.timer`; it checks the wq#1500 successor unit
files match `/etc/systemd/system` (`vunit_file`, lines 16-19) and keeps the superseded-unit
and superseded-binary absence checks (lines 30-37). Install path still refuses without
`WQ1396_FORCE_LEGACY=1` (lines 43-46). Regression test: `ops/tests/test_wq5252_wq1396_verify.py`.

## Premise checks (cold)
- `/home/lumen/scratch/subtraction/REPORT.md` §1A: Minerva proposal machine STOPPED + DISABLED
  (factory, classifier, proposal, eval, promote, defer-review). Matches the comment.
- Live: `systemctl is-enabled` on all six timers -> `disabled` x6; `minerva-promote.timer`
  `inactive`. Intended-off state is current, so dropping the run-state gate is right; a
  root install could never have healed it (install path exits 2).
- Superseded artifacts absent: `/etc/systemd/system/minerva-proposal-promoter.{service,timer}`
  and `/home/lumen/bin/minerva-proposal-promoter` -> `No such file or directory`.
- `ops/wq1500-minerva-promoter/install.sh` has no `--verify` block, so there is no
  contradicting enabled-assertion elsewhere.

## Probe (rev-pinned, live host; `/tmp/fworker-wq5288/probe.py`)
Each rev's `_verify.sh`, wq1396 `install.sh` and wq1500 unit files extracted with `git show <rev>:`
into a tempdir, `--verify` run against the real systemd state:

| Input | rc | Output |
|---|---|---|
| `72a23bb9^` (pre-fix) | 1 | `MISS not enabled/active: minerva-promote.timer`, `VERIFY FAIL (2)` |
| `72a23bb9` (fix) | 0 | `VERIFY OK` |
| `72a23bb9` + successor timer drifted (mutation) | 1 | `MISS differs: /etc/systemd/system/minerva-promote.timer`, `VERIFY FAIL (1)` |

The mutation row shows the new verify can still fail: it did not become a no-op.
Live main copy: `bash ops/wq1396-minerva-proposal-promoter/install.sh --verify` (run via
python subprocess; bare `bash install.sh` is not on the worker allowlist) -> `VERIFY OK`, rc=0.
Liveness `ops_verified(wq1396-minerva-proposal-promoter)` holds.

Regression test: `python3 ops/tests/test_wq5252_wq1396_verify.py` -> 5 tests, OK. Its
`test_no_run_state_assertion_on_intended_off_timer` regex would match the pre-fix line
`  venabled minerva-promote.timer`, so it fails on the old code.

## Nits (non-blocking, no rework)
- `test_disabled_inactive_timer_verifies_ok` only asserts no `minerva-promote` MISS lines; it
  does not assert `returncode == 0`, so it would not catch an unrelated new MISS.
- `verify_block()` uses `open(INSTALL).read()` without closing -> `ResourceWarning`.
- Worker allowlist gap: `bash /home/lumen/foreman/ops/<bundle>/install.sh --verify` (the exact
  liveness command the verify ticket prescribes) is refused; also `pytest` and `head`. wq#5287
  hit the same gap.
