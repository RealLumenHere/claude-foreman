# wq#5320 verify of wq#5274 (PR #2750, commit 99664240): CONFIRMED

Target: `ops/wq3349-sentinel-watch/install.sh`. The `SENTINEL_AUDIT_ON=0` intent flag (install.sh:12) gates the timer-state checks in `--verify` (install.sh:26-30) and `enable --now` in install (install.sh:40-47).

## What I checked and how

| Claim | Evidence | Result |
|---|---|---|
| PR is on main | `git merge-base --is-ancestor 99664240 origin/main` | IN_MAIN |
| The timer is intended-off, not just broken | `/home/lumen/scratch/subtraction/timers-disable-list.txt:73` = `lumen-sentinel-audit`. `REPORT.md:67` lists it under "Watchers-of-watchers / duplicate probes (8)" | true |
| Nothing else re-enables it | Grep `lumen-sentinel-audit` in `/home/lumen/bin`: no matches. In foreman, only the bundle, its README and the wq5274 test mention it | true |
| Live timer state matches the intent | `systemctl is-enabled` = disabled; `is-active` = inactive | matches intended-off |
| vfile: binary deployed | python `filecmp.cmp(shallow=False)` bundle `sentinel-watch` against `/home/lumen/bin/sentinel-watch` | identical |
| vunit_file: .service (the drift that wq#5274 reported) | filecmp against `/etc/systemd/system/lumen-sentinel-audit.service`; `diff` also clean | identical |
| vunit_file: .timer | filecmp against `/etc/systemd/system/lumen-sentinel-audit.timer` | identical |
| vflag `--check` | installed binary is byte-identical to the bundle, which defines `--check` in argparse (`/home/lumen/bin/sentinel-watch:411`). `os.access(X_OK)` = True | passes |
| Shipped tests | `python3 -m pytest -q ops/tests/test_wq5274_sentinel_audit_intended_off.py`: 6 passed. This includes `test_live_host_has_no_timer_state_miss`, which runs the real `install.sh --verify` on Lumen | pass |
| The probe can fail | `test_flag_on_disabled_timer_still_misses`: with the flag set to 1 and the timer dead, the test expects 3 MISS lines and rc != 0 | yes |

## Attempts to break it

- **Is the change hiding real drift?** No. Unit files, the binary and `--check` are still verified whatever the flag says. Only the timer state is skipped, and that state is deliberately off per the subtraction list.
- **Can the env flip the intent?** No. The flag is a plain assignment (install.sh:12), not `${SENTINEL_AUDIT_ON:-0}`, so an environment variable cannot change it. To re-enable, someone has to commit a change, which is a reviewed decision.
- **Is install still safe to run as root?** Yes. `systemctl enable --now` appears exactly once, inside the flag gate (the test asserts this). The old ops-root-apply advice ("run install.sh as root") would no longer undo the subtraction.
- **Minor, not a defect:** README.md:3 still says "run 4x daily by lumen-sentinel-audit.timer". The new README.md:4-5 qualifies that straight away. The rollback echo (install.sh:50) still disables the timer, which does no harm when it is already off.

## Limitation

This worker's allowlist refused a direct `bash /home/lumen/foreman/ops/wq3349-sentinel-watch/install.sh --verify` (and `cmp`/`sha256sum`). Each of its five checks was reproduced separately (see above). The real script also ran under pytest's live-host test, which asserts no timer-state MISS. Every other check it performs was reproduced as passing, so its expected exit code is 0 (VERIFY OK).

Verdict: **CONFIRMED.** No contradiction, so nothing to escalate.
