# wq#1404 — deploy: wq#1397 gitea-version-check systemd timer

**Status: DEPLOYED.** wq#1397/PR #278 added `ops/wq1397-gitea-version-check/` (script,
service, timer, install.sh) but `install.sh` had never been run on this host — confirmed
during wq#1400 verify-pass (`systemctl status gitea-version-check.timer` → "could not be
found"). Root cause: this host's local `/home/lumen/foreman` clone of `main` was stale
(`f08b737`) relative to `origin/main` (`c366056`), so the merged wq#1397 files weren't even
present on disk locally, on top of `install.sh` never having been invoked.

## What was done

1. `git -C /home/lumen/foreman fetch origin && git -C /home/lumen/foreman merge --ff-only
   origin/main` — fast-forwarded the local `main` clone from `f08b737` to `c366056`,
   bringing in `ops/wq1397-gitea-version-check/install.sh` (and `ops/wq1396-...`) which were
   merged upstream but absent locally.
2. `/home/lumen/bin/lumen-run start --root -- bash
   /home/lumen/foreman/ops/wq1397-gitea-version-check/install.sh` — ran the install script
   as root via the detached systemd-run wrapper per the script's own documented invocation
   (`ops/wq1397-gitea-version-check/install.sh:3`), which copies
   `gitea_version_check.py` to `/home/lumen/services/gitea-version-check/`, installs the
   `.service`/`.timer` units to `/etc/systemd/system/`, and runs `systemctl daemon-reload`
   + `systemctl enable --now gitea-version-check.timer`
   (`ops/wq1397-gitea-version-check/install.sh:14-16`).

## Evidence

`systemctl list-timers` now shows:

```
Fri 2026-09-04 17:40:13 UTC   23h   Thu 2026-09-03 17:40:13 UTC   5s ago   gitea-version-check.timer   gitea-version-check.service
```

`systemctl status gitea-version-check.timer` reports `Loaded: loaded
(/etc/systemd/system/gitea-version-check.timer; enabled; preset: enabled)` and `Active:
active (waiting)`. The CVE-2026-66877 regression monitor described in wq#1397/PR #278 is
now actually running (first scheduled run at boot+10min, then every 24h, per
`ops/wq1397-gitea-version-check/install.sh:18`).

## Related gap (not in scope here, filed as follow-up)

`ops/wq1298-gitleaks-detect/install.sh` shows the same pattern: its `.timer` is also absent
from `systemctl list-timers` on this host (`gitleaks-detect.timer` → "could not be found").
Not fixed as part of wq#1404 — flagging for a separate ticket rather than scope-creeping
this deploy.
