# wq#5341 — verify of wq#5326 (PR #2792): provider-watch --verify fails open on unknown systemctl state

Verdict: **CONFIRMED.** I found no contradiction.

Diff under test: commit `d5ea5056` (already on main), which touches
`ops/wq3336-provider-watch/install.sh` and `ops/tests/test_wq5273_provider_watch_intended_off.py`.

## What the fix does

- `ops/wq3336-provider-watch/install.sh:37-41`: if `systemctl is-enabled` or `is-active`
  prints nothing, the script prints an `ERR ... state unknown` line, sets `_V_ERR=1` and
  calls `vdone`.
- `ops/_verify.sh:182-185`: `vdone` checks `_V_ERR` before `_V_MISS`, so the result is exit 2
  `VERIFY ERROR`. That is the contract's "check could not run" code (`ops/_verify.sh:9`).
  Because the guard exits early, the OFF branch at `install.sh:48-60` can no longer be
  reached with empty input.

## Attempts to break it

| Probe | Result |
|---|---|
| `python3 -m pytest -q ops/tests/test_wq5273_provider_watch_intended_off.py` | 10 passed in 0.43s |
| Original repro `python3 reports/wq5321_verify_wq5273_probe.py` | `unreachable-systemctl rc=2 :: VERIFY ERROR` (before the fix this was rc=0 VERIFY OK) |
| Both answers empty (silent shim, exit 1) | test `test_unreachable_systemctl_is_err_not_off`: rc 2, no OFF line |
| Only `is-active` empty (`is-enabled` answers) | test `test_empty_is_active_alone_is_err`: rc 2. The `\|\|` catches either side being empty. |
| Can the test fail? Remove the guard (`if false`) | test `test_unreachable_systemctl_old_branch_would_pass`: the same shim reads `OFF unknown/unknown`. The new tests do catch the old behaviour. |
| ERR vs MISS ordering (unit-file MISS lines printed before the guard) | `vdone` checks ERR first, so the result is exit 2, not 1. This is correct: if the state is unknown, the check itself is suspect. |
| `set -u` with `_V_ERR` | `_V_ERR` is initialised at `ops/_verify.sh:25` before the guard runs. Safe. |
| Real failure modes: no bus, not booted with systemd, binary missing | systemctl writes all of these to stderr, which `2>/dev/null` drops. stdout is then empty and the guard fires. |
| Offline/chroot `is-enabled` answers but `is-active` cannot | This is the half-silent case above: ERR. |

## Gaps (not regressions, nothing filed)

- If some systemctl wrapper printed its error text to **stdout**, the text would be taken as a
  real state and go down the OFF branch. Stock systemctl never does this, so it is out of scope.
- `enabled-runtime/inactive` and `masked/inactive` still read as OFF/OK. wq#5321 already
  judged that correct (`reports/wq5321_verify_wq5273*.md:35`).
- House style: the author's commit `d5ea5056` has a `Co-authored-by:` trailer, which
  wq#4796 forbids. It was already merged and does not change behaviour.

## Live liveness

I could not run `bash /home/lumen/foreman/ops/wq3336-provider-watch/install.sh --verify`
from this worker. The per-command allowlist refused it (and the same command on the
worktree copy). The harness's own `ops_verified(wq3336-provider-watch)` check is what
proves liveness. wq#5326's own record says `DEPLOYED: wq3336-provider-watch: --verify OK`.
The fix only adds an exit for empty input. With the real Lumen state (disabled/inactive,
both answers non-empty) the guard does not fire, so the live verdict is the same as before.
