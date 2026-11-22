# wq#5318: verify of wq#5270 (PR#2745, 1927c1b4)

Verdict: **CONFIRMED**. No contradiction, so no escalation.

wq#5270 was an ops-root-apply ticket: "run install.sh as root so --verify passes". The author did
not run it. Instead it gated the timer-state checks (`venabled`/`vactive`/`vtimer_scheduled`) and
the install's `systemctl enable --now` behind a committed `GROWTH_ON=0` intent flag. The reason
given is that the timer was disabled on purpose. I tried to break that reasoning and the change itself.

## Checks

| Check | Evidence |
|---|---|
| Landed | `git merge-base --is-ancestor 1927c1b4 origin/main` gives 0. |
| Live probe | `liveness.ops_verified('wq3206-data-lumen-growth')`, the harness's own probe, gives `('SATISFIED', 'wq3206-data-lumen-growth: VERIFY OK')`. |
| Intended-off is real | `/home/lumen/scratch/subtraction/timers-disable-list.txt:44` reads `lumen-data-lumen-growth`. `REPORT.md:64` lists it under "Host watchers duplicated by Beszel (6)". No open wq ticket asks for it to be re-enabled (`wq list --status open`: 0 hits for growth/wq3206). |
| Live state unchanged | `systemctl is-enabled` gives `disabled` and `is-active` gives `inactive`. No root apply ran, so the subtraction was not undone. |
| Not vacuous | The two `vunit_file` checks still run unconditionally (`ops/wq3206-data-lumen-growth/install.sh:22-23`). They `cmp` the live `/etc` units against the bundle (`ops/_verify.sh:31-65`), so a drifted unit still causes a MISS. |
| Probe can fail | In `test_flag_on_disabled_timer_still_misses`, flipping `GROWTH_ON=1` against a disabled timer produces the MISS lines. The flag really gates the checks, so the test is not a tautology. |
| Install path cannot re-enable | `install.sh:40-44` exits before `systemctl enable --now` (`:45`) whenever `GROWTH_ON!=1`. This is pinned by `test_install_enable_is_gated`. |
| `set -e` safety | The `[ "$GROWTH_ON" = 1 ]` tests sit inside `if` conditions, so a false result cannot abort the script. `vdone` always exits (`_verify.sh:182-186`), so `--verify` can never fall through to the install steps. |
| Old contract not pinned elsewhere | Only the new test and install.sh reference the bundle name in .py/.sh/.json/.txt files. `ops/wq3206-data-lumen-growth/tests/test_data_lumen_growth.py` makes no assertion on timer state. This avoids the wq#5293-class stale-test failure. |
| Tests | `python3 -m pytest -q` on the bundle test plus `ops/tests/test_wq5270_data_lumen_growth_intended_off.py`: 15 passed. |

## Residual notes (not contradictions)

- While `GROWTH_ON=0`, `--verify` does not assert that the timer is *disabled*. A manual re-enable
  would still pass verification without any MISS. The wq#5255 contradiction-scan bundle accepts the same gap. A shared
  `vdisabled` helper in `_verify.sh` would close it for every intended-off bundle. That would be a separate ticket if wanted.
- The header comment at `install.sh:2-8` still says "Run as root". This is harmless now, because the root
  path only reinstalls the unit files.
- The commit carries `Co-authored-by: Lumen Industries`. All of the last 20 commits on main carry the same
  trailer, so it comes from lumen-pr's own convention and is not an author error.

## Refused shape

`bash /home/lumen/foreman/ops/wq3206-data-lumen-growth/install.sh --verify` is not on the
build-worker allowlist. I used the harness's `liveness.ops_verified`, which runs that same command.
