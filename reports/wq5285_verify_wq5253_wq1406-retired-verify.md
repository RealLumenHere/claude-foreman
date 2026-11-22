# wq#5285 — verify of wq#5253 (PR #2719, commit 7cdabfb9): CONFIRMED

Target: `ops/wq1406-paid-fallback-guard/install.sh --verify` stops asserting
`venabled`/`vactive` on `lumen-budget-alert.timer` and `lumen-deepseek-spend.timer`
(intended-off since the 2026-10-08 subtraction); now checks retired units absent +
replacement unit files present. No root apply was done or needed.

## Probes (2026-11-22, Lumen)

| # | Probe | Result |
|---|---|---|
| 1 | Live `bash /home/lumen/foreman/ops/wq1406-paid-fallback-guard/install.sh --verify` | exit 0, `VERIFY OK` |
| 2 | Live state: `systemctl is-enabled/is-active` both timers | `disabled` / `inactive` (intended-off, matches the new model) |
| 3 | `ls /etc/systemd/system/` | both replacement `.timer` files present (root, Aug 23 / Jul 12); `paid-spend-tripwire.{service,timer}` absent |
| 4 | Negative probe (can fail): `WQ1406_UNIT_DIR=<empty tmp dir>` on the deployed copy | exit 1, `VERIFY FAIL (2)`, both `MISS replacement unit file absent` — the check is live, not vacuous, and the deployed copy is the new code |
| 5 | Install path without `WQ1406_FORCE_RETIRED_DEPLOY` | exit 2 (refusal intact) |
| 6 | `pytest ops/tests/test_wq5253_retired_verify.py` | 6 passed (incl. retired-unit-present → MISS, missing-replacement → MISS, live-host OK) |
| 7 | Cited evidence: `scratch/subtraction/timers-disable-phase2.txt:13`, `timers-disable-list.txt:35` | lines read `lumen-budget-alert.timer` and `lumen-deepseek-spend` — citations exact |
| 8 | Cross-check `ops/deploy_triage.py --check` (register line 244-252 lists the same two units for wq1406, checks unit-file presence only) | `OK 36 bundles, 0 problem(s)` — consistent with the new verify |
| 9 | Regression: retired-unit absence check kept vs parent (`git show 7cdabfb9^:...install.sh`) | preserved; only the dir became overridable |

## Attempts to break it — none landed

- Weakening: the verify can no longer notice the paid drain is unpaged. That is the
  honest state (both timers off on purpose) and the PR says so in the header
  (install.sh:22-24) and README; asserting live timers was what made the verify
  permanently red. Not a defect.
- `WQ1406_UNIT_DIR` seam: a stray env var could point verify at a fake dir. Test-only,
  named per-bundle, no caller sets it (grep). Acceptable.
- Masked units: a masked timer is a `/etc/systemd/system/<u> -> /dev/null` symlink;
  `test -e` follows it and `/dev/null` exists (checked: exit 0), so a masked
  replacement would still read "present" although no longer re-enableable without
  unmask. Narrow gap (systemctl refuses to mask while the real file sits in /etc, so it
  needs a delete-then-mask); not worth a rework. Hardening if ever touched: `[ -f ]`
  plus `! -L`, or `systemctl is-enabled` != `masked`.

## Minor, non-blocking

- `ops/deploy_triage.py:248-252` reason text still says the drain "is already paged by"
  the two timers; wq#5253 corrected that claim in install.sh but not in the register.
  Prose only, `--check` unaffected.

## Out of scope finding (not wq#5253)

Full `pytest ops/tests/`: 418 passed, 3 failed — all in
`ops/tests/test_wq1609_scan_verify.py` (`test_reads_both_timer_states`,
`test_timer_not_enabled_is_a_miss`, `test_timer_not_active_is_a_miss`). Cause:
c866b57d (wq#5255, same pattern on wq1609) removed the venabled/vactive assertions but
left the old tests. Belongs to wq#5293 (open verify of wq#5255).

Verdict: CONFIRMED. wq1406 `--verify` is live-green for the right reason.
