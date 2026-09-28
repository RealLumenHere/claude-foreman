# wq#2845: verify-pass of wq#2510 (MacPro6,1 fan-floor alarm)

**Verdict: CONFIRM.** wq#2510 does what its ticket and done-notes claim. Ran the full
offline suite and, unlike the prior (killed) attempt at this ticket, the live `check`
against real netdata end to end — it returns a correct verdict for the one node
currently reachable and degrades safely (never a false clear) for the three that
aren't.

## What wq#2510 shipped (PR#1166, branch `fix/wq2510-...`, commit `966096b`, not yet
merged to `main`)

One commit, four files, 3340 insertions, 0 deletions:
- `ops/fan_floor_alarm.py` — `check`/`replay` CLI. Sliding 60-sample (1/min) window;
  raises when the window's Te0T mean is `> 80C` while fan1's max in that window is
  `< 1000 rpm`; clears only when Te0T mean drops `< 78C` or the fan leaves the floor.
  A short/gapped window returns `None` from `window_stats`, and `next_state` carries
  the prior firing state on `None` — a gap can never manufacture a clear.
- `ops/fan_floor_alarm.yaml` — thresholds (80/78/1000/60min) plus per-node netdata
  targets, `action: hold|notify`, `hold_subsystem`. `thor: {netdata: null, action:
  notify}`.
- `ops/tests/test_wq2510_fan_floor_alarm.py` + `ops/tests/fixtures/wq2510_isis_0918_0919.csv`
  — a 49-hour, minute-resolution replay of the real ISIS_POWEROFF_RCA.md event, plus
  synthetic rule/config-validation cases.

## Adversarial checks run

1. **Offline suite, independent run.** Extracted the four shipped files from `966096b`
   into a scratch dir (not trusting the author's own "Testing" section) and ran
   `python3 ops/tests/test_wq2510_fan_floor_alarm.py -v`: **15/15 pass**, including
   `test_fires_on_the_boot_minus_4_event_and_holds_through_the_cut` (fires exactly once
   at `2026-09-18T19:56Z`, no clear before the boot-4 power cut — the RCA event) and the
   five `ThorCarveOut` tests (drop-thor, hold-thor, hold_subsystem-alone, rename-by-IP,
   and check() never handing thor a hold_subsystem — all refused/asserted correctly).

2. **Live `check --json` against real netdata — the exact step the prior wq#2845
   attempt never completed.** Its critique blamed a curl exit 7 for a dead run. Ran it
   directly this time:
   ```
   isis:200  hathor:000  nephthys:000  khepri:000   (raw curl to each :19999/api/v1/charts)
   ```
   `python3 ops/fan_floor_alarm.py check --json` on that same reachability:
   - `isis`: `raise=false clear=true te0t_mean=75.2 fan_max=1114.0` — sane (mbpfan
     holding the fan at ~1114 rpm per wq#2508, comfortably under 80C).
   - `hathor`/`nephthys`/`khepri`: `raise=null clear=null reason="RuntimeError: ...
     netdata did not answer"` — **null, not a false clear**, exactly as `check()`'s own
     docstring promises. Exit code **0**, no crash, on both `--json` and plain text
     output.
   - `thor`: `action=notify reason="no netdata source"`, `hold_subsystem=null` — carve-out
     holds live, not just in the unit test.

   The three unreachable nodes are a pre-existing, already-logged environmental gap
   (`kb/ISSUE-LOG.md` row 868(a), OPEN before this ticket and still open — MacPro netdata
   not reachable from Lumen on the tailnet), **not** something wq#2510 introduced or
   should have fixed; its own ticket scope was the alarm logic, and `check()` handles
   that gap the only correct way a read-only alarm can: report "no verdict" with a
   reason, never guess a clear.

3. **Chart resolution against the live isis endpoint** (the suffix-matching the code
   exists to protect against hwmon-index drift, ISIS_POWEROFF_RCA.md:242):
   `resolve_charts('100.100.129.24:19999')` →
   `['sensors.temperature_applesmc-isa-0300_temp98_Te0T_input',
   'sensors.fan_applesmc-isa-0300_fan1_Main_input']` — exactly one hit each, real chart
   IDs, not fixture data.

4. **Live `replay`** over the last 4 hours on isis: exit 0, zero transitions — consistent
   with the live `check` verdict (already clear, nothing to transition).

5. **Cross-checked the wq#2484 hand-off contract this ticket claims to feed.**
   `wq show 2484`'s own notes (written by wq#2510 at done-time) say the consumer should
   read `check --json`'s `raise`/`clear`/`action`/`hold_subsystem`, treat `null` as
   never-a-clear, and only `action=hold` may drive an automatic hold — `notify` (Thor)
   must never trigger one. That description matches the shipped code exactly; not a
   restated claim, checked against `check()`'s actual return shape above.

6. **Acceptance criteria from wq#2510 itself** ("alarm defined, fired once against the
   retained 09-18/09-19 isis window ..., Thor notify-only carve-out asserted in the
   config"): all three met — see items 1 and the yaml's `validate()`-enforced thor entry.
   wq#2510's dependency wq#2508 (mbpfan) is `status: done`.

No break found. The prior attempt's blocker (couldn't reach netdata, never finished
live validation) does not reproduce — isis answers, the script handles the other three
nodes' unreachability exactly as designed, and nothing in the diff needs a fix.

## Disposition

- wq#2510: **CONFIRM**. Board updated via this report; no escalation.
- `kb/ISSUE-LOG.md` row 868(a) remains the tracker for the separate, pre-existing
  hathor/nephthys/khepri netdata-reachability gap — out of this ticket's scope.
