# wq#858 — Trivy weekly CVE scan on Lumen

Single static binary (Trivy, Apache-2.0), offline vulnerability DB, weekly systemd timer.
Scans every running container image plus the Lumen filesystem itself; writes a markdown
report to `/home/lumen/maint/trivy/` every run and fires an ntfy digest only when a
HIGH/CRITICAL finding is present.

Ref: `reports/opsaudit_a12_tooling_consult.md` section 2, rank 2.

## Why this wasn't installed live by the BUILD-lane worker

Same wall as `ops/wq857-victorialogs-bes` and `ops/wq859-renovate-gitea`: this harness's
`guard.sh` unconditionally denies every root-elevation verb (`sudo`, and by extension
anything that needs it) for a fworker BUILD-lane session, and its Bash allowlist has no
mutating `systemctl`/binary-install entries either — confirmed this session: `systemctl
enable`/`start` are absent from `harness/worker-settings.json`'s allowlist (only
`status`/`is-active`/`is-enabled`/`show`/`list-*`/`cat` are), and `docker` is denied for
every subcommand except `ps`/`logs` (`harness/guard.sh`). `install.sh` therefore installs
the binary to `/usr/local/bin` and enables the timer — both root-only — so it can only be
staged here, not run, from this sandbox. Trivy's release version and asset naming
(`trivy_0.74.0_Linux-64bit.tar.gz`, checksums file `trivy_0.74.0_checksums.txt`) were
confirmed live against `api.github.com/repos/aquasecurity/trivy/releases/latest` this
session — not guessed — and the host is `x86_64` (`uname -m`, confirmed live).

## Deploy (root — `lumen-run --root` on Lumen itself)

```bash
/home/lumen/bin/lumen-run start --root -- bash /home/lumen/worktrees/wq858-a5/ops/wq858-trivy-weekly-scan/install.sh
```

Downloads Trivy v0.74.0, verifies its sha256 against the release's own checksums file,
installs to `/usr/local/bin/trivy`, primes the offline vulnerability DB, stages
`trivy-scan.sh` + `trivy_report.py` under `/home/lumen/services/trivy-scan/`, creates
`/home/lumen/maint/trivy/` (owned `lumen:lumen` so reports are readable without root),
and enables `trivy-scan.timer`.

## What runs weekly

`trivy-scan.service` (`Type=oneshot`, `User=root` — needed for `docker.sock` plus
unrestricted filesystem read on `trivy rootfs /`) runs `trivy-scan.sh`, which:

1. `trivy image --download-db-only` — refreshes the offline CVE DB. This is the only
   network-dependent step; everything after runs `--skip-db-update`.
2. `trivy image` against every unique image in `docker ps --format '{{.Image}}'`.
3. `trivy rootfs /` against the Lumen filesystem, skipping `/proc /sys /dev /run` (not
   real files), `/home/lumen/worktrees` (ephemeral agent checkouts, not host state), and
   `/var/lib/docker` (already covered per-image in step 2 — scanning it again via rootfs
   would just double-count the same layers).
4. `trivy_report.py` — flattens every per-target JSON output into
   `/home/lumen/maint/trivy/trivy_scan_<date>.md` (severity counts + a HIGH/CRITICAL
   findings table) and, only if that table is non-empty, sends one ntfy digest
   (`-t lumen-alerts -p high -T trivy-weekly-scan`, matching the topic/priority
   convention in `/home/lumen/bin/ntfy-send` and `ops/wq853-fleet-maint-run`).

`trivy-scan.timer`: `OnCalendar=weekly`, `RandomizedDelaySec=30min` (avoid a fleet-wide
thundering-herd if other weekly timers share the calendar slot), `Persistent=true` (a
missed run because Lumen was down fires on next boot instead of silently skipping a week).

## Verify (after `lumen-run --root` deploy)

```bash
systemctl status trivy-scan.timer
systemctl start trivy-scan.service    # force an immediate run
journalctl -u trivy-scan.service -n 200 --no-pager
cat /home/lumen/maint/trivy/trivy_scan_*.md
```

Expect a report file every run, and an ntfy notification on the `lumen-alerts` topic only
on weeks with a HIGH/CRITICAL finding — a clean week is silent by design (see
`test_send_digest_is_noop_when_nothing_high_plus` below).

## Tested this session (no root, no live trivy binary — sandbox has neither)

```bash
python3 -m pytest ops/wq858-trivy-weekly-scan/test_trivy_report.py -v
# 9 passed
```

`test_trivy_report.py` exercises `trivy_report.py` — JSON flattening across multiple scan
outputs, malformed/non-JSON files tolerated, severity counting, the HIGH/CRITICAL-only
findings table, the ntfy digest firing exactly on a HIGH+ finding and staying silent
otherwise, and `main()`'s dated report file — against fixtures shaped like real `trivy
--format json` output (`ArtifactName`/`Results`/`Target`/`Vulnerabilities`). This is the
one piece of the ticket a headless, non-root sandbox can actually run and verify; the
shell orchestration (`trivy-scan.sh`) and the install itself (`install.sh`) are correct by
inspection and by the same pattern `wq857`/`wq859` used, but weren't — couldn't be — run
live here. Needs Frank or a claude-web session to run the `lumen-run --root` deploy above,
then a real `systemctl start trivy-scan.service` to confirm end-to-end against the live
Docker fleet and filesystem.

## Uninstall

```bash
systemctl disable --now trivy-scan.timer
rm -f /etc/systemd/system/trivy-scan.{service,timer} /usr/local/bin/trivy
rm -rf /home/lumen/services/trivy-scan
systemctl daemon-reload
```

(`/home/lumen/maint/trivy/` reports are left in place — they're just files, not state
this service needs back to reinstall.)
