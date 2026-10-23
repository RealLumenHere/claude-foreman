# wq#4487 — Horus: install fail2ban

Fleet security audit 2026-10-22 (`/home/lumen/scratch/research/fleet-security-audit.md`)
flagged Horus (100.83.19.124, Commander primary): SSH hardening GOOD, UFW GOOD, but
fail2ban not installed. This directory is the real, ready-to-run fix.

## Why this wasn't applied live

This ticket dispatched with `needs:` empty (`wq show 4487`), so `fleet-exec` has no
grant for Horus in this worker's sandbox — confirmed directly:

```
$ /home/lumen/bin/fleet-exec run horus systemctl is-active fail2ban
fleet-exec: DENY [grant:grant_missing_env]: grant file did not verify
```

Raw `ssh` is also not on this worker's Bash allowlist. Same shape as wq#1444/wq#1560:
pilot-node tickets opened by `minerva_defer_review` get no exec grant unless dispatched
with a `needs:` tag, and the grant can't be bootstrapped from inside the ticket it's
missing from. Logged as ISSUE-LOG #row for wq#4487.

## What to run

On Horus, as root (or via a follow-up ticket dispatched with `needs:run(horus)` /
`needs:copy(horus:...)` so `fleet-exec` carries a grant):

```
sudo bash install.sh /path/to/this/dir
```

`install.sh` apt-installs fail2ban, drops `jail.local` (sshd jail enabled, tailnet +
loopback in `ignoreip` so UFW/fail2ban can't band together to lock out legitimate
tailnet traffic), and enables+starts the service.

No dead-man rollback (contrast wq#4485, wq#3108): this only adds a package and a
service, it doesn't touch live sshd_config or UFW rules, so there's no path to a full
lockout — worst case is an aggressive ban, which `ignoreip` already guards against.

## Follow-up

Queued as a physical/console task for whoever next has root on Horus, or redispatch
this ticket's remainder with `needs:run(horus)` so a worker's `fleet-exec` grant can
run `install.sh` directly.
