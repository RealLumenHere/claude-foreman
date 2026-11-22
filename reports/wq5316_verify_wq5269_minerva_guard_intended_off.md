# wq#5316 verify: wq#5269 (PR #2742) wq3147-minerva-guard intended-off timer

Verdict: **CONFIRM**, with one residual hazard filed as a follow-up (not a contradiction of the PR's claim).

## What the PR claims
`ops/wq3147-minerva-guard/install.sh --verify` stops asserting `venabled`/`vactive` on
`lumen-minerva-guard.timer`, which the 2026-10-08 subtraction turned off on purpose. The PR does not root-enable the timer.

## Checks (run 2026-11-22 ~12:20 UTC, as lumen, on Lumen)
- Landed: PR head cc2341d35b squash-merged as 06a7cbaf. `git diff cc2341d35b 06a7cbaf` shows no difference on the bundle or the test, and `git diff 06a7cbaf origin/main` shows no drift on those paths since the merge.
- Intent evidence: `/home/lumen/scratch/subtraction/timers-disable-list.txt:4` reads `lumen-minerva-guard`, and `phase2-results.txt:4` reads `lumen-minerva-guard.timer: inactive/disabled svc=inactive`. The cited lines exist and say what the PR claims.
- Live state: `systemctl is-enabled` reports `disabled` and `is-active` reports `inactive` for the timer, which matches the intent.
- Live verify: `bash ops/wq3147-minerva-guard/install.sh --verify` exits 0 with `VERIFY OK`. The harness probe `liveness.ops_verified('wq3147-minerva-guard')` returns `SATISFIED`.
- Tests: `pytest ops/tests/test_wq5269_minerva_guard_intended_off.py ops/wq3147-minerva-guard/tests` gives 9 passed. The wq5269 test includes a falsifiable arm (re-inserting the old asserts makes the test flag them).
- Verify still bites (my own break attempt): I copied the bundle and `_verify.sh` to a tmpdir and appended one line to `minerva_guard.py`. Verify then returned rc 1 with `MISS differs: /home/lumen/services/lumen-minerva-guard/minerva_guard.py`. Drifting the `.timer` unit file instead also gave rc 1 with `MISS differs: /etc/systemd/system/lumen-minerva-guard.timer`. So the PR removed the timer-state asserts without hollowing out the file and unit checks.

## Residual hazard (follow-up wq#5325, not a contradiction)
- `install.sh:33` still runs `systemctl enable --now lumen-minerva-guard.timer` unconditionally. The PR's own comment (`install.sh:19-21`) names this as the way the subtraction gets undone.
- The sibling fixes for the same class gate that line behind an in-file intended-on flag: wq#5266 (8cec2566, iptables-drift-guard) and wq#5268 (9e3eaf33, issue-log-hygiene). wq#5269 did not.
- Concrete route to the hazard: any future change to `minerva_guard.py` makes `vfile` fail. That files an ops-root-apply saying "run install.sh as root", and doing so re-enables the timer.
- `README.md:8` still says the timer "runs ... every 6h", which is stale.
- Scope of the PR was the --verify failure, and that is fixed. The install-path gap is a follow-up.
