# wq#1680 — Adversarial verify-pass of wq#1658 (universal timer dead-man via auto cron-stamp drop-in)

**Verdict: CONFIRM** — `claude-foreman` PR #418 (commit `2912369`, `ops/wq1658-cron-stamp-dropin/`)
and its companion `lumen-bin` PR #88 (commit `f106cf7`, already merged to `lumen-bin` main)
hold under independent re-run against the live fleet and adversarial code reading. No
fabricated numbers, no unguarded production mutation, no bug found.

## What was shipped

- `claude-foreman` PR #418: `lumen_cron_stamp_dropin_gen.py` (182 lines) — scans real
  (non-symlink) `*.timer` files directly under `/etc/systemd/system`, resolves the
  service each activates (`Unit=` line, else same-name `.service`), and reports/writes a
  `<service>.service.d/99-cron-stamp.conf` drop-in adding
  `ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n` for every uncovered, non-opted-out
  unit. `plan` is read-only (default); `apply` writes + `daemon-reload`.
- `lumen-bin` PR #88 (separate repo, referenced but not itself part of #418's diff):
  wires `lumen-cron-stamp-dropin-gen apply` into `unit-drift`'s `cmd_snapshot()` behind a
  `try/except (OSError, TimeoutExpired)` with `timeout=60`, and splices the
  `wadjet-ha-monitor.timer` / `lumen-backup-critical.timer` LastTrigger-age lines into
  `handoff`'s `verify()`.

## Claim-by-claim check

1. **"Read-only in `plan` mode, matches unit-drift's fleet-vs-distro placement test."**
   Ran `lumen_cron_stamp_dropin_gen.py plan` directly against the live fleet (no `sudo`,
   no writes): 135 timers scanned, 8 covered, 0 opted out, 2 service-missing, 125
   uncovered (README's 132/8/2/122 — the fleet grew by 3 timers since the PR was
   authored 2026-09-10; every ratio and category is otherwise identical). Confirmed
   `os.path.exists('/etc/systemd/system/a5-health.service.d')` is `False` before and
   after both `plan` and `apply --dry-run` runs — no writes occurred. Read
   `/home/lumen/bin/unit-drift`'s `scan()`: it tags direct `SYS` entries `kind:"file"`
   vs `kind:"symlink"`, matching the PR's claim that a real (non-symlink) file at that
   path is the fleet-vs-distro signal.

2. **"2 skipped are `lumen-foreman-gate@{nightly,window}.service`, already covered by
   hand."** Reproduced exactly via `plan()`'s `skipped["service-missing"]`. Read
   `/etc/systemd/system/lumen-foreman-gate@.service` (the template the two instances
   fall back to): it already carries
   `ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n` inline. The generator's
   "service-missing" label is technically imprecise (the instance file doesn't exist,
   but its template does and is covered) — not a coverage gap, matches the README's
   framing exactly.

3. **"Not run against production this session; only wired for a future manual
   `apply`."** `lumen-cron-stamp-dropin-gen` already exists at `/home/lumen/bin/` and is
   byte-identical (`diff` clean) to the PR's source — PR #88 is merged. `unit-drift`'s
   `cmd_snapshot()` does call `apply`, guarded as claimed. But the deployed
   `unit-drift.timer` only runs `unit-drift check --digest` (read-only), never
   `snapshot` — grepped every `/etc/systemd/system/*.service` and the user crontab for
   `unit-drift snapshot`: zero automated callers exist. `apply` only fires when an
   operator runs `unit-drift snapshot` by hand, which hasn't happened since #88 merged
   (still 125 uncovered, not 0). Claim holds: no autonomous mass mutation of 125 fleet
   services has occurred or can occur without a human running `snapshot`.

4. **`handoff` LastTrigger snippet correctness.** `systemctl show wadjet-ha-monitor.timer
   -p LastTriggerUSec --value` returns a real timestamp; `date -d "<that string>" +%s`
   parses it cleanly and the resulting age (~26s at time of test) is sane.
   `/home/lumen/bin/handoff` (deployed) contains the spliced block verbatim.

## Edge cases probed, no break found

- Bare invocation (no subcommand) and `apply --dry-run` both defaulted to report-only
  behavior with zero writes, as documented.
- Checked every current fleet timer's resolved service name for a non-`.service` target
  (would misfire `ExecStartPost` into a unit with no `[Service]` section): none exist
  today.
- `already_covered()` / opt-out regex logic read correctly; idempotent by construction
  (re-running `apply` after drop-ins exist finds them via `already_covered`).

No contradiction, no opus-5 escalation needed.
