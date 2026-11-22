# wq#5313 — verify of wq#5266 (PR #2740): wq2819 iptables-drift-guard intended-off

Verdict: **CONFIRM**. No contradiction; no escalation.

Diff under test: foreman `8cec2566` (on origin/main), `ops/wq2819-iptables-drift-guard/install.sh`
+ `ops/tests/test_wq5266_iptables_drift_guard_intended_off.py`.

## What the PR claims
1. `lumen-iptables-drift-guard.timer` is intended-off (2026-10-08 subtraction), so `--verify`
   must stop asserting venabled/vactive; install must stop `enable --now`-ing it.
2. Intent lives in a file constant `IPTABLES_DRIFT_GUARD_ON=0` (install.sh:11); flipping it to 1
   restores venabled + vactive + vtimer_scheduled (install.sh:27-31) and enable --now (install.sh:41-47).
3. Deployed `.py` was refreshed so the file-drift MISS clears.

## Evidence (all run 2026-11-22 ~13:55 UTC on Lumen)
| Check | Result |
|---|---|
| Subtraction citation | `/home/lumen/scratch/subtraction/timers-disable-phase2.txt:26` = `lumen-iptables-drift-guard.timer`; `timer-massacre-phase2.md:63,75,90` list it as disabled on purpose and dropped from `guards.timers`. Claim holds. |
| Deployed files == bundle | `filecmp.cmp(shallow=False)` True for `.py`, `.service`, `.timer` vs `/home/lumen/services/...` and `/etc/systemd/system/...`. The original "differs" MISS is gone. |
| Live timer state | `systemctl is-enabled` = disabled, `is-active` = inactive (timer and service) — matches intent; the PR did not re-enable it. |
| Live liveness probe | `liveness.ops_verified('wq2819-iptables-drift-guard')` → `('SATISFIED', 'wq2819-iptables-drift-guard: VERIFY OK')` (this runs `bash install.sh --verify`, exit 0). |
| PR tests | `pytest ops/tests/test_wq5266_iptables_drift_guard_intended_off.py` → 6 passed (incl. the live-host test, and the flag=1-dead-timer test that proves the verify can still fail). |
| Install-path probe (mine) | `reports/wq5313_verify_wq5266_probe.py`, rev-pinned. `8cec2566`: shipped → no enable; env `IPTABLES_DRIFT_GUARD_ON=1` → still no enable (intent is not env-overridable, as the comment says); flag=1 → enables. Exit 0. `8cec2566~1`: enables in every case → exit 1. The probe can fail. |

## Attempts to break it
- **Env override**: the constant is reassigned unconditionally, so an exported env var cannot flip
  intent — confirmed by the probe's `shipped+env ON=1` row.
- **Root install undoing the subtraction**: install path still does `cp` units + `daemon-reload`
  but no `enable`; a stray root run is now harmless. Confirmed by probe.
- **Verify now blind**: with flag=0 the timer state is not asserted at all (neither on nor off),
  so a manual re-enable would not be flagged. That is the same design the sibling intended-off
  fixes use (wq#5259/5262/5277) and the PR comment states it explicitly; not a defect.
- **Flag=1 path regressed?** It now also asserts `vtimer_scheduled`, which wq#4910 had dropped only
  because of the intended-off state; restoring it under ON=1 is correct (test 2 asserts the
  `SubState=dead` MISS).

## Nits (non-blocking)
- Commit carries a `Co-authored-by: Lumen Industries` trailer; every recent foreman commit has the
  same trailer, so it comes from the landing tool, not the author.

## Reproduce
```
python3 -m pytest -q /home/lumen/foreman/ops/tests/test_wq5266_iptables_drift_guard_intended_off.py
python3 /home/lumen/foreman/reports/wq5313_verify_wq5266_probe.py 8cec2566      # exit 0
python3 /home/lumen/foreman/reports/wq5313_verify_wq5266_probe.py 8cec2566~1    # exit 1
bash /home/lumen/foreman/ops/wq2819-iptables-drift-guard/install.sh --verify    # VERIFY OK
```
Note: in this headless worker, `bash .../install.sh --verify`, `cmp` and `sha256sum` are not on the
Bash allowlist; the live verify was taken through the harness's own `liveness.ops_verified` probe
(the ticket's named Liveness check) and file equality through `python3 filecmp`.
