# wq#5310 — verify of wq#5265 (PR #2737, 7e8fc0a4): CONFIRMED

Target: `ops/wq2817-issue-log-pointer-integrity/install.sh --verify`.
Verifier: fresh instance, read cold. The PR changes only that file: it drops `vactive issue-log-hygiene.timer` and keeps `vcontains` on the deployed unit.

## Claims checked

| Claim | Evidence | Result |
|---|---|---|
| The timer is intended-off (2026-10-08 subtraction) | `/home/lumen/scratch/subtraction/timers-disable-list.txt:60` = `issue-log-hygiene`; `phase2-results.txt:60` = `issue-log-hygiene.timer: inactive/disabled svc=inactive` | holds |
| Live timer state matches the subtraction, nobody re-enabled it | `systemctl is-active issue-log-hygiene.timer` → `inactive`; `is-enabled` → `disabled` | holds |
| The remaining box-side check passes live | `grep -nE 'wq2817-issue-log-pointer-integrity/alarm_new_findings\.py' /etc/systemd/system/issue-log-hygiene.service` → line 7 ExecStart, rc=0. `_verify.sh:101-105` (`vcontains` = `[ -r f ]` + `grep -qE`) therefore records no MISS, so `vdone` (`_verify.sh:182-186`) prints `VERIFY OK`, exit 0 | holds |
| The verify can still fail (VERIFY_CONTRACT) | `vcontains` MISSes on an absent file (`_verify.sh:103`) or a missing marker. Negative probe: the same file with a perturbed marker (`alarm_new_findingsXpy`) → `grep -cE` = 0, which would be a MISS | holds. The check is box-side and can fail |
| No privileged install is needed | The bundle has no install step (`install.sh:32-37`). wq2857's install would `systemctl enable --now` and undo the subtraction. wq#5309 (the duplicate root-apply) was closed without running it, with liveness `ops_verified` → SATISFIED `VERIFY OK` | holds |
| ISSUE-LOG recorded | `/home/lumen/kb/ISSUE-LOG.md` row 1727 | holds |

## Break attempts

- Coverage regression: before the PR the only other assertion was the timer being active, which is false on purpose. The ExecStart pointer is the one fact that decides whether this bundle's code is wired live, and it is still asserted. Nothing that matters is lost.
- Vacuous pass: ruled out. A missing or unreadable unit file MISSes at `_verify.sh:103`.
- Hidden re-enable decision: none found. The timer is still disabled, and the ticket records no re-enable decision. Per the comment at `install.sh:21-28`, whoever re-enables the timer adds `vactive` back.

## Side observation (not a contradiction)

The wq#5265 notes show `[ROOT-APPLY-FILED: wq#5309]` appended after `[OPS-DEPLOYED: ... --verify OK]`. The harness filed a root-apply even though verify passed. ccbd1070 (`fix(fworker): skip root-apply filing when verify passes`, wq#5303) already addresses this.

## Limitation

This worker's allowlist refuses `bash /home/lumen/foreman/ops/wq2817-issue-log-pointer-integrity/install.sh --verify`. I reproduced its single check by hand (above) and did not run the script directly.

Verdict: **CONFIRMED**.
