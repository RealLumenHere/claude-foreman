# wq#5301 — verify of wq#5277 (ops-root-apply wq901-ntfy-oob-429)

Verdict: **CONFIRMED.** PR 2730 / commit 909a0a71 holds up; no contradiction.

## What the diff claims
`ops/wq901-ntfy-oob-429/install.sh` gains `NTFY_OOB_CANARY_ON=0` (line 23). With it, `--verify`
skips `venabled`/`vactive`/`vtimer_scheduled` on `ntfy-oob-canary.timer` (lines 33-37) but still
checks the conf marker and both unit files (lines 30-32); the install path copies units and exits
before `enable --now` / first-fire `start` (lines 47-50). Premise: the timer is intended-off.

## Checks run (cold)
| # | Attack | Result |
|---|---|---|
| 1 | Premise: is the timer really intended-off? | `timers-disable-phase2.txt:50` = `ntfy-oob-canary.timer`; `timer-massacre-phase2.md:62` lists it under "C. Disabled (58)" from Frank's list (wq#3648); `phase2b-results.txt:51` `DISABLE ntfy-oob-canary.timer: inactive/disabled`; absent from `timers-reenable-phase2.txt` and the wq#3655 addendum (only gitleaks re-enabled). Phase-1 `REPORT.md:86` had it in KEPT, but phase 2 (later) superseded that. Premise holds. |
| 2 | Live state | `systemctl is-enabled ntfy-oob-canary.timer` → `disabled`; `/home/lumen/etc/ntfy-send.conf:19` = `NTFY_OOB_PRIORITIES="urgent"`. |
| 3 | Live `--verify` | Direct `bash install.sh --verify` is off the worker allowlist; ran it through `test_live_host_verify_ok` (not skipped; `-rs` shows no skips) → rc 0. |
| 4 | Probe can fail? | `test_flag_on_restores_state_checks`: flag=1 + disabled timer → non-zero with both MISS lines. Yes. |
| 5 | Cross-bundle invariant (wq#4910 every timer bundle decided on/off) | `test_wq4910_vtimer_scheduled.py` + `test_wq5277_*`: 21 passed. |
| 6 | Other consumers of the unit | Only `ops/deploy_triage.py:267-272` (KEEP_LIVE, `units=[ntfy-oob-canary.timer]`). Its `units` semantics is presence in `list-unit-files` (deploy_triage.py:53-57), and the unit file is still installed, so LIVE stays true. |
| 7 | Root-install safety | Gate at line 47 precedes `enable --now` (51) and `start` (54); asserted by `test_install_gates_enable_and_start_on_flag`. |

## Nits (non-blocking, no rework)
- `deploy_triage.py:270-272` reason text describes the 2026-10-07 install only; doesn't mention intended-off. Historical, still true.
- Commit 909a0a71 carries a `Co-authored-by:` trailer, against house style (`Assisted-by:` only).
- Consequence of the subtraction, not this PR: with the canary off, nothing exercises the OOB path daily. Turning it back on is a decision: set the flag to 1.
