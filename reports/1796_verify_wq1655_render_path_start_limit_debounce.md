# wq#1796 — Adversarial verify-pass of wq#1655 (fleet-inventory-render.path debounce)

**Verdict: CONTRADICT** — the ticket's root-cause writeup misidentifies which systemd
knob actually failed, and a second, independently-checkable claim in the same writeup
("the new limit does not itself require a reset-failed the way the old default
StartLimit did") is directly contradicted by `man systemd.path(5)` on this host. Net
effect: the staged fix does not eliminate the "`.path` units never self-recover"
fragility the ticket exists to fix — it only raises the trigger threshold at which the
identical permanent-failure mode recurs (5-in-10s -> 20-in-10s), while removing the
generous systemd-default headroom (200-in-2s) on the one knob that remains active.

## What was shipped

Commit `a41d11f` (feat branch), landed on main as `a7b2136` (current HEAD of this
verify branch, content-identical for wq#1655's files — `git diff a41d11f a7b2136`
touches only unrelated later tickets). All three fix parts are staged, not deployed
(`/etc/systemd/system` and `daemon-reload`/`reset-failed`/`start` are outside the
authoring worker's sandbox — reasonable and disclosed in DESIGN.md's "Why staged, not
live-edited" section):
`ops/wq1655-fleet-inventory-render-path-debounce/{DESIGN.md,RUNBOOK.md,units.diff,
ISSUE_LOG_ENTRY.md,staged/lumen-failed-units,tests/test_path_unit_urgent.sh}`.

## Probe run

`tests/test_path_unit_urgent.sh` via the RUNBOOK's prescribed `python3 -c
"import subprocess,sys; sys.exit(subprocess.run(['bash','tests/test_path_unit_urgent.sh']).returncode)"` —
all 6 assertions `ok`, `ALL PASS`, exit 0. This probe only covers fix part 3
(`lumen-failed-units` gains `--type=path` + urgent classification); it does not
exercise parts 1-2 (the actual `units.diff` systemd config) at all — those are
unverified by any automated check, staged for manual verification per RUNBOOK.md.
I did that manual verification below.

## Claims checked against live Lumen systemd state (fresh, 2026-09-11)

**Accurate:**
- `fleet-inventory-render.path` is live `loaded failed failed`, `Result=unit-start-limit-hit`,
  `InactiveEnterTimestamp=Thu 2026-09-10 05:39:45 UTC` — matches the ticket's claimed
  incident time exactly.
- Live `systemctl cat` of both units matches `units.diff`'s pre-image byte-for-byte
  (neither `StartLimitIntervalSec=` nor `TriggerLimitIntervalSec=`/`TriggerLimitBurst=`
  is present yet) — the diff applies cleanly against current reality.
- `lumen-failed-units` (`/home/lumen/bin/lumen-failed-units:30`) really does omit
  `--type=path` from its `systemctl list-units` call, and wq#1657 really is not yet
  live (`grep -c is_finding_exit` -> 0) — part 3's premises hold, and the staged copy
  is syntactically valid (`bash -n`, via the sandboxed subprocess wrapper) and diffs
  cleanly against the live file as described.
- systemd on this host is `259.5-0ubuntu3.4` (`systemctl show -p Version`), well above
  the `TriggerLimitIntervalSec=`/`TriggerLimitBurst=` floor (added in v250) — no
  version-compat gap.

**Wrong:**
- DESIGN.md/ISSUE_LOG_ENTRY.md: *"the actual failure was the path unit's own default
  start limit, not the service's (13 triggers in 15s > 5/10s)."* This is backwards.
  `man systemd.path(5)`, DESCRIPTION section, verified on this host: *"As protection
  against busy looping in this trigger/start cycle, a start rate limit is enforced on
  **the service unit**, see StartLimitIntervalSec= and StartLimitBurst=... Unlike other
  service failures, the error condition that the start rate limit is hit is propagated
  **from the service unit to the path unit** and causes the path unit to fail as well."*
  Live confirmation: `fleet-inventory-render.service` (not the path unit) currently
  carries the unmodified `StartLimitIntervalUSec=10s`/`StartLimitBurst=5`
  (`systemctl show fleet-inventory-render.service -p StartLimitIntervalUSec -p
  StartLimitBurst` — Restart=no, so this is purely the trigger/restart-cycle limiter
  the man page describes). The "5/10s" figure the ticket cites is real, but it belongs
  to the service, exactly as the man page says and exactly as the ticket denies. The
  fix's `StartLimitIntervalSec=0` on the service is nonetheless the correct, load-bearing
  change (it matches the man page's prescribed remedy) — the diagnosis is wrong, the
  patch for that specific line happens to be right anyway.
- units.diff's own "Notes" section: *"[TriggerLimit] does not itself require a
  reset-failed the way the old default StartLimit did."* Directly contradicted by the
  same man page, three lines later: *"If the limit is hit, the unit is placed into a
  failure mode, and will not watch the paths anymore until restarted."* Hitting
  `TriggerLimitBurst=20`/`TriggerLimitIntervalSec=10s` fails the `.path` unit exactly
  as permanently as the old StartLimit path did — `systemctl reset-failed` + `start`
  is still required, `.path` units still have no self-recovery. The ticket's own title
  ("dead from start-limit... no debounce") is not resolved by this change; its failure
  threshold is moved from >5 triggers/10s to >20 triggers/10s, nothing more.
- Numeric consequence of the above: default `TriggerLimitIntervalSec=2s`/
  `TriggerLimitBurst=200` (confirmed via `man systemd.path(5)`, "Added in version 250")
  is ~50x more permissive per second than the staged override (200/2s = 100/s vs.
  20/10s = 2/s). Once `StartLimitIntervalSec=0` disables the service-level guard, the
  path-level `TriggerLimit` becomes the *only* remaining rate limiter — and this patch
  makes that sole remaining limiter tighter than systemd's own out-of-the-box default,
  not just "wide enough" as claimed. A future bulk fleet-inventory edit bigger than the
  one that caused this incident (e.g. a >20-file batch write within 10s, plausible for
  the kind of fleet-onboarding sweep `wq#1580` was) reproduces the exact same
  permanent-death symptom this ticket was filed to eliminate.

## Why this matters, not just pedantry

The ticket's premise is that `.path` units "never self-recover," which is exactly why a
worker needs a debounce, not merely a wider window. The shipped design swaps a
5-trigger/10s ceiling for a 20-trigger/10s ceiling and asserts (incorrectly) that
crossing the new ceiling is benign. It is not: crossing it reproduces the identical
`unit-start-limit-hit`-style permanent failure, just at 4x the burst size. A future
on-call reading DESIGN.md's claim that "exceeding it does not itself require a
reset-failed" would reasonably conclude the recurrence risk is gone; it is only
smaller.

## What's correct and should survive

- `StartLimitIntervalSec=0` on `fleet-inventory-render.service` — correct fix, matches
  documented systemd behavior, safe given `Restart=no` (no runaway-restart risk).
- Part 3 (`lumen-failed-units` `--type=path` + urgent classification) — correct,
  tested, composes cleanly with wq#1657 as designed.
- `TriggerLimitIntervalSec=10s`/`TriggerLimitBurst=20` on the `.path` unit is not
  wrong to add, but its stated rationale is wrong and its value is tighter than the
  systemd default it replaces, for a knob that was never the active limiter in the
  original incident. A wider burst (or leaving `TriggerLimitBurst`/`IntervalSec` at
  systemd's own default, now that the service-level guard is disabled) would give the
  same protection against a true runaway loop without reintroducing a bounded-but-real
  version of the exact bug this ticket set out to close.

## Verdict

CONTRADICT. Escalating per archetype-8 protocol: contradiction → opus-5@high.
