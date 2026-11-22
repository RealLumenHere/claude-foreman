# wq#858 / wq#4899 — Trivy weekly CVE scan on Lumen (single source)

This bundle is the single source for the weekly Trivy scan that is **actually live** on
Lumen. wq#4899 (split from wq#4719 item 2) copied the running files in byte for byte and
retired the root variant this bundle used to ship, which was never deployed.

| bundle file | live target | notes |
|---|---|---|
| `scan.sh` | `/home/lumen/services/trivy-weekly/scan.sh` | 0755, owner lumen |
| `lumen-trivy-weekly.service` | `/etc/systemd/system/` | `Type=oneshot`, `User=lumen`, `TimeoutStartSec=1800` |
| `lumen-trivy-weekly.timer` | `/etc/systemd/system/` | `Sun *-*-* 09:00:00` UTC, `Persistent=true`, 300s random delay |
| `SHA256SUMS` | n/a | pins the three files above |

Not shipped, only checked by `--verify`:
- `/home/lumen/bin/trivy` (v0.74.0). The binary is a prerequisite, not payload.
- `/etc/systemd/system/lumen-trivy-weekly.service.d/99-cron-stamp.conf`, the dead-man
  drop-in (`ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n`). Its only author is
  `lumen-cron-stamp-dropin-gen` (wq#1658), and `cron_deadman` (wq#1184) reads the stamp.
  A oneshot only runs `ExecStartPost` on success, and `scan.sh` exits 1 when the JSON
  report is missing, under 2 KB or unparseable (wq#2858). So a failed scan leaves
  `/home/lumen/logs/.stamp.lumen-trivy-weekly` stale and the dead-man pages. That
  property is why the drop-in must stay.

## What a run does (`scan.sh`)

1. `trivy image --download-db-only`: refreshes the vulnerability DB.
2. `trivy image --severity HIGH,CRITICAL` on every local image
   (`docker images`, `<none>` excluded).
3. `trivy rootfs --scanners vuln --timeout 45m --skip-dirs …` on `/`, once as a table and
   once as JSON. The skip list keeps the scan out of worktree, archive, cache and
   `/data/lumen` trees. wq#2858: walking them hit a semaphore deadline that FATAL'd every
   report from 08-23 to 09-27.
4. Writes `reports/trivy_<UTC stamp>.{txt,json,json.err}` and prunes reports older than 90 days.
5. Sends ntfy on `lumen-fleet`: high priority on FAILED or any CRITICAL, default otherwise.

Evidence that it works, as of 2026-11-22: `reports/` holds valid runs from 2026-11-01, -05,
-08 and -15. Each has a 64-77 MB JSON report and an empty `.json.err`. The 11-15 journal
shows `Deactivated successfully` and "6min 4.8s CPU time over 5min 4.8s wall clock".

## Verify (read-only, any user)

```bash
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq858-trivy-weekly-scan/install.sh --verify'
```

`--verify` exits 0 only when all of the following hold:
- the pin in `SHA256SUMS`, the bundle copy and the live target have the same sha256 for
  `scan.sh` and both units;
- the live `scan.sh` is executable;
- the timer is enabled, active and has a finite next elapse;
- the cron-stamp drop-in is present;
- `trivy rootfs --help` still offers `--skip-dirs`, `--scanners` and `--timeout`;
- the retired root variant is still absent (`trivy-scan.service`, `trivy-scan.timer`,
  `/home/lumen/services/trivy-scan`).

If anything fails, it prints one `MISS` line per problem and exits 1. If it cannot read
something it needs, it exits 2.

Tests: `python3 -m pytest ops/tests/test_wq4899_trivy_bundle.py -q`. They run the real
box once, then a temp fixture with each drift injected.

## Changing the scan

1. Edit `scan.sh` or a unit here, then re-pin it: `sha256sum scan.sh lumen-trivy-weekly.* > SHA256SUMS`.
2. Land the change through lumen-pr.
3. Deploy as root:
   `ssh lumen@100.117.66.102 '/home/lumen/bin/lumen-run start --root -- bash /home/lumen/foreman/ops/wq858-trivy-weekly-scan/install.sh'`.

The install checks the bundle against `SHA256SUMS`, installs `scan.sh` as lumen and the
units as root, removes any leftovers of the root variant, runs `daemon-reload` and
`enable --now` on the timer, and runs `unit-drift accept --ticket wq#4899` for exactly
these two units (wq#3579). Last, it re-runs `--verify`.

Never hand-edit the live copy. `--verify` reports that as a MISS, and the next install
would overwrite it.

## Retired: the root variant (wq#858 original, never deployed)

`trivy-scan.sh`, `trivy-scan.service` (`User=root`), `trivy-scan.timer` and
`trivy_report.py` with its tests were the original design. That design downloaded the
binary to `/usr/local/bin`, wrote reports to `/home/lumen/maint/trivy` and alerted on
`lumen-alerts`. It was staged on 2026-08-23 but never installed. The lumen-user scan
above went live instead, and wq#2985 marked this bundle SUPERSEDED on 2026-09-28.
wq#4899 deleted those files. If you need them, they are in git history before this
commit. `ops/deploy_triage.py` now lists the bundle as LIVE, with `trivy-scan.*` in
`absent_units`.

## Fixed by wq#5239 (were known gaps after wq#4899)

- **Timeout headroom.** The unit's `TimeoutStartSec` was `1800` (30 minutes for the
  whole oneshot), shorter than either of the two `trivy rootfs --timeout 45m` calls, so a
  slow walk would be killed by systemd before Trivy's own timeout fired. It is now `4h`:
  2 x 45m rootfs, plus the per-image scans (Trivy's default 5m each) and the DB update.
  Measured runs take about 5 minutes, so this only matters when something is wrong.
- **ntfy password in argv.** The old `curl -u "lumen:$(grep NTFY_PASS …)"` calls put the
  ntfy password in curl's argv, where `/proc/<pid>/cmdline` exposes it to local users.
  `scan.sh` now has one `ntfy_send` helper that reads the password into an unexported
  shell variable and pipes `user = "lumen:…"` to `curl -K -` with the bash `printf`
  builtin, so no process argv holds it. The value is escaped for curl's config syntax
  (backslash, double quote), and `cut -d= -f2-` keeps a password that contains `=`.
  `ops/tests/test_wq5239_trivy_ntfy_argv.py` runs the helper against a local HTTP server
  with a curl shim that logs argv, and checks the password reaches the Authorization
  header but appears on no argv.

## Known gaps

- The HIGH and CRITICAL counts are a `grep -c` over the text report, so they are
  approximate (the script labels them that way).
