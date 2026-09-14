# wq#1926 — lumen-kernel-watch: Linux 7.3 data-loss fix tracker

## Problem

[Phoronix reported](https://www.phoronix.com/news/Linux-7.3-Fix-Silent-Data-Loss)
a silent user-space data-loss bug, present since 2023, fixed in Linux
7.3-rc3. Lumen runs Ubuntu 26.04 LTS on a Dell T5810; nothing on the fleet
tracked the host kernel version or watched for the fix landing in Ubuntu's
kernel — no kernel-version watcher existed in the systemd unit list, and
`renovate-gitea.timer` (`ops/wq859-renovate-gitea`) only covers Gitea-hosted
dependency manifests, not the host kernel (minerva proposal `87bba83ff853`,
factory plan `4d0341eeb400`).

## What this adds

`lumen-kernel-watch` — a script deployed to `/home/lumen/bin/lumen-kernel-watch`
(oneshot, run via `lumen-kernel-watch.service`/`.timer`, daily +0-30min
jitter) that:

1. Reads the running kernel (`uname -r`).
2. Reads Ubuntu 26.04's apt candidate kernel (`apt-cache policy
   linux-image-generic`).
3. Alerts via `/home/lumen/bin/ntfy-send` (the house alert path — see
   `ops/wq1373-cf-token-liveness/cf_token_liveness.py:52,181` for the same
   convention) at `high` priority only when the candidate is >= 7.3.0 (has
   the fix) and the running kernel is older.

Read-only: it never runs `apt upgrade` and never reboots. The actual kernel
bump stays a manual, Frank-gated `apt` step, per the ticket's own
constraint — this unit only shortens the time-to-notice.

## Deploy

RED lane (new_service, `\breboot\b` pattern matched in the factory plan
notes even though the unit itself never reboots) — factory will not
auto-apply. Install by hand as root via `lumen-run --root` on lumen itself:

```
lumen-run start --name wq1926-install --root -- bash /home/lumen/foreman/ops/wq1926-kernel-watch/install.sh
lumen-run wait <UNIT>
lumen-run log <UNIT>
```

## Rollback

```
systemctl disable --now lumen-kernel-watch.timer 2>/dev/null
rm -f /etc/systemd/system/lumen-kernel-watch.service /etc/systemd/system/lumen-kernel-watch.timer /home/lumen/bin/lumen-kernel-watch
systemctl daemon-reload
```

No host state besides the unit files and the script itself is touched by
the watcher — nothing else to roll back.
