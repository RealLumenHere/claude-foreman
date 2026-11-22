# wq#5281 — verify of wq#5256 (PR #2715): wq1640 intended-off timer

Verdict: **CONFIRM**. Hardening follow-up filed as wq#5322. It does not contradict the fix.

## What wq#5256 shipped

Merged commit `32f2a028`. It touches `ops/wq1640-lumen-state-render-frank-side-hardware-q-a3/install.sh:23-29` and the README section "Timer is intended-off". It drops `venabled`/`vactive` for `prefs-block-check.timer` from the `--verify` block. It keeps `vfile` for `prefs_block.py` and `vunit_file` for both unit files. It does not run `install.sh` as root.

## Evidence

| Check | Result |
|---|---|
| Liveness `ops_verified(wq1640-…-a3)` against the live host, through `harness/liveness.py:432` | `SATISFIED … VERIFY OK` |
| Live timer state (`systemctl is-enabled` / `is-active`) | `disabled` / `inactive`. The fix leaves the subtraction intact. |
| Unit files byte-match live | Yes. `/etc/systemd/system` is not a git repo, so `_vsuperseded` (`ops/_verify.sh:53-62`) cannot launder a drift there. Only `cmp` passes it. |
| Module | Passes through `_vsuperseded`: the minerva repo moved on from the bundle copy. This matches the done-notes. |
| Bundle changed after the fix? | No. `git log 32f2a028..HEAD -- ops/wq1640… ops/_verify.sh` is empty. |

### The probe can fail

`reports/wq5281_verify_wq5256_probe.py` rebuilds the bundle and `_verify.sh` from rev-pinned git objects. It runs them through the harness's own `liveness.ops_verified`:

- A: fix `32f2a028` → `SATISFIED` (PASS)
- B: parent `32f2a028^` → `STILL_BLOCKED`, `MISS not enabled … MISS not active … VERIFY FAIL (2)` (PASS). This reproduces the original failure.
- C: fix plus a mutated bundle `.timer` → `STILL_BLOCKED`, `MISS differs: /etc/systemd/system/prefs-block-check.timer` (PASS). The verify still detects unit drift.

## What could not be checked

`/home/lumen/scratch/subtraction/timers-disable-list.txt:59` was not read. The read was held by hold_gate (grant request 5, class H1). The intended-off premise has independent support:
- wq#4910's comment in the parent commit already called the timer intended-off and skipped `vtimer_scheduled`.
- The live timer state is disabled/inactive.
- ISSUE-LOG #1703 and #1705 describe the same subtraction class.

## Weaknesses (not contradictions)

1. **The root-apply footgun is still there.** `install.sh:50` still runs `sudo systemctl enable --now prefs-block-check.timer` with no guard. The only warning is in the README. If `--verify` fails again for any other reason, for example unit-file drift, the next `[ops-root-apply]` ticket would again say "run as root" and undo the subtraction.
2. **`--verify` no longer notices a re-enable.** It passes whether the timer is on or off.

The sibling fix wq#5273 (`5bdd3605`) covers both points with an exit-2 refuse guard plus a test. Filed **wq#5322** to port that pattern.

3. The merged commit `32f2a028` has a `Co-authored-by:` trailer, which is against house style (wq#4796). The pre-merge commit `821cc180` has none, so the merge step probably added it. This is cosmetic.
