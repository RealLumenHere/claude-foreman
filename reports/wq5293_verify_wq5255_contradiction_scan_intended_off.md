# wq#5293: verify of wq#5255 (PR#2722, c866b57d)

Ticket under test: wq#5255 `[ops-root-apply] wq1609-minerva-contradiction-scan`
Verifier: fresh instance, archetype 8. Date: 2026-11-22.

Verdict: **CONTRADICT** (escalates per ticket policy to opus-5-5@high judgment).
The live target is fine and the design call holds up. The diff is still incomplete:
it changed the bundle's `--verify` contract and left the bundle's own regression test
asserting the old contract, so that test fails 3 of 6 on main.

## What the diff does

`ops/wq1609-minerva-contradiction-scan/install.sh:13-20` (c866b57d) drops
`venabled`/`vactive lumen-contradiction-scan.timer` from `--verify` and leaves the
three `vfile`/`vunit_file` checks (lines 10-12). The README gets a paragraph saying the
timer is intended-off. The full install path is unchanged: line 40 still runs
`sudo systemctl enable --now`, and the new comment and README say so.

## Checks that held

| Check | Evidence |
|---|---|
| Landed | `git merge-base --is-ancestor c866b57d origin/main` gives 0. Bundle dir unchanged on main since. |
| Live probe | `liveness.ops_verified('wq1609-minerva-contradiction-scan')` (the harness's own probe, which runs `bash install.sh --verify`) gives `('SATISFIED', '...: VERIFY OK')`. |
| Script byte-match | `git hash-object` of the bundle's `lumen_contradiction_scan.py` and of `/home/lumen/bin/lumen-contradiction-scan` are both `b3cf7b97…`. |
| Units match | `systemctl cat` of the .service/.timer matches `git show HEAD:` of the bundle copies. The only extra is the generated `99-cron-stamp.conf` drop-in, which is a separate file and is not compared. |
| Intended-off is real | `/home/lumen/scratch/subtraction/timers-disable-list.txt:25` reads `lumen-contradiction-scan`. wq#2888 notes say "Minerva stopped + contradiction-scan timer disabled". Live state: `is-enabled` disabled, `is-active` inactive. |
| Pre-fix verify could not pass | The removed `venabled`/`vactive` checks against the live disabled/inactive timer give exactly the two MISS lines quoted in wq#5255. |
| No root apply ran | wq#5292 was closed with "No root apply needed, none run". The timer is still disabled. |

## Contradiction: stale regression test, red on main

`ops/tests/test_wq1609_scan_verify.py` (wq#2182, c664979f) pins the old contract:

- `:69-72 test_reads_both_timer_states`: asserts `--verify` calls `is-enabled`/`is-active`.
- `:74-77 test_timer_not_enabled_is_a_miss`: asserts rc 1 + `MISS not enabled`.
- `:79-82 test_timer_not_active_is_a_miss`: asserts rc 1 + `MISS not active`.

c866b57d did not touch this file. Result on current main:

```
$ python3 /home/lumen/foreman/ops/tests/test_wq1609_scan_verify.py
FAIL: test_reads_both_timer_states   AssertionError: 'is-enabled lumen-contradiction-scan.timer' not found in ''
FAIL: test_timer_not_active_is_a_miss  AssertionError: 0 != 1 : VERIFY OK
FAIL: test_timer_not_enabled_is_a_miss AssertionError: 0 != 1 : VERIFY OK
Ran 6 tests ... FAILED (failures=3)
```

These failures follow directly from the lines the diff removed, so the diff caused them.
The sibling intended-off fixes from the same sweep each shipped a test that flips the
contract (`ops/tests/test_wq5269_minerva_guard_intended_off.py`,
`test_wq5254_langfuse_lag_intended_off.py`, and others). wq#5255 shipped no test, and the
existing test now disagrees with the code. It landed red because `lumen-pr land` does not
run `ops/tests/*`.

Recommended fix (not done here, since a verify pass does not re-implement): flip the
three tests to the intended-off contract. `--verify` should issue no `systemctl
is-enabled/is-active` for the timer, and disabled/inactive should give `VERIFY OK`.
Keep the read-only and marker-order tests. Logged as ISSUE-LOG #1739.

## Looked for and not found

- A hidden consumer that needs the timer on: none. wq#2888's measurement work was closed
  as superseded because of the disable.
- A verify that now passes vacuously: no. The three file checks still use `cmp`. A drifted
  script would MISS, and so would a drifted unit (`ops/_verify.sh:31-42`).
- Residual hazard, by design and documented: running the full `install.sh` as root
  still re-enables the timer (`install.sh:40`). The README warns against it, and
  `--script-only` covers scanner redeploys.

## Tooling note

The ticket's own confirm command, `bash /home/lumen/foreman/ops/<bundle>/install.sh
--verify`, is refused by the build-worker allowlist. `sha256sum` on `/home/lumen/bin`
and `/etc` targets is refused too. This is already logged as ISSUE-LOG #1693. I used
`liveness.ops_verified` plus `git hash-object`/`systemctl cat` instead.
