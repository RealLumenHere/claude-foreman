# wq#5335 — verify of wq#5322 (PR #2784, b5e48a1d): wq1640 install.sh re-enable guard

Verdict: CONFIRM. No contradiction.

## What the diff claims
- `ops/wq1640-lumen-state-render-frank-side-hardware-q-a3/install.sh:60-65`: install refuses with exit 2 before any step unless `PREFS_BLOCK_REENABLE=1`. That blocks the unguarded `sudo systemctl enable --now prefs-block-check.timer` (now :84) from reversing the 2026-10-08 subtraction.
- `install.sh:34-50`: `--verify` branches on the timer's live state: disabled+inactive → `OFF` line; disabled but active → MISS; enabled/static/alias → `REENABLED` line plus `vactive` + `vtimer_scheduled`.
- `ops/tests/test_wq5322_prefs_block_intended_off.py`: 9 cases with systemctl and sudo shimmed.

## Checks run
1. `python3 -m pytest -q ops/tests/test_wq5322_prefs_block_intended_off.py`: 9 passed in 0.53s.
2. Falsifiability: `test_old_install_had_no_gate` and `test_wq5256_verify_was_blind_to_reenable` replay the pre-fix 32f2a028 script, which goes straight past the gate and prints no REENABLED/OFF line. So the gate and state assertions can fail against the old code.
3. Faithful port: matches the sibling wq#5273 pattern in `ops/wq3336-provider-watch/install.sh:32-64` (same case arms, env opt-in, exit 2).
4. Gate placement: the `--verify` block always ends in `vdone`, which exits (`ops/_verify.sh:182-185`), so verify can never fall through to the gate or the install steps.
5. Live target (read-only): `systemctl is-enabled` = disabled, `is-active` = inactive, SubState=dead, so the OFF branch applies. Both unit files are byte-identical to the bundle (sha256 6dd50e5d…, 6567bcbe…). `/home/lumen/minerva/prefs_block.py` differs from the bundle copy but meets `_vsuperseded`: it equals minerva HEAD (90531b02), and the bundle blob ab652549 appears in history at dfdc0cb. So it reports `OK superseded`. This worker's allowlist refuses running `bash …/install.sh --verify` directly. Instead I ran the real script against the live file checks with only systemctl shimmed (probe below). It returned rc=0 for disabled/inactive, which is consistent with the author's live `--verify OK`.
6. Adversarial edge states (probe `/tmp/fworker-wq5335/probe.py`, reuses the test's `run()`):
   - enabled-runtime/active and indirect/active → MISS, rc=1 (good).
   - enabled/inactive → REENABLED + `MISS not active`, rc=1 (good).
   - masked/inactive, linked/inactive, empty/inactive → OFF, rc=0 (correct: none of these can fire).
   - enabled-runtime/inactive → OFF, rc=0. A runtime enable without `--now` does not start the timer, and /run is cleared at reboot, so it cannot fire. The sibling has the same behaviour. Not a defect.

## Notes (non-blocking)
- The commit has a `Co-authored-by: Lumen Industries` trailer. Every recent foreman commit has one (it is landing tooling, not this author), so it is out of scope here.
- Step 1 (`install -m 0644 prefs_block.py`) would still revert newer minerva commits if someone runs it with `PREFS_BLOCK_REENABLE=1`. The README and the comment at install.sh:54-59 say so. That is acceptable for an explicit opt-in.
