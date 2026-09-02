# wq#358 — Sia n100-efficiency pack canary (soak-gated on Bes)

**VERDICT: STAGED for Sia, not deployable by this session. Zero changes made to Sia —
this session could not even read-verify Sia, let alone install. This is the 4th+
attempt at wq#358 to hit the identical structural wall, now confirmed once more and
documented as such rather than retried or faked past.**

## Why this session can't run the install, or even verify Sia read-only

This is a repeat of `kb/ISSUE-LOG.md` #39 and wq#415's Sobek finding, reproduced again
this attempt:

- The fworker BUILD-lane profile for this session (`harness/worker-settings.json`) has
  **no `ssh` allowlist entry at all** — confirmed by `grep -n "ssh" harness/worker-settings.json
  worker-settings-general.json` returning zero matches in this worktree. This is
  stricter than the wq415/Sobek session, which had ssh and used it to capture a live
  baseline (`ops/wq415-sobek-n100-efficiency-canary/baseline-sobek-pre.md`).
- `/home/lumen/n100-efficiency` (the staged wq#357 pack itself) is outside this
  worktree's `--add-dir` set — a direct `ls` on it was refused by the allowlist, same
  as issue #39 recorded for the prior attempt.
- `install.sh` hard-requires root on the target (per wq#415's finding), and guard.sh
  unconditionally denies any `sudo` token, local or SSH-embedded, with no exception.
- Net effect: this session cannot reach Sia at all, not even to read sysfs/systemctl
  state, let alone write systemd/sysctl.d/udev/modprobe.d files as root.

No new workaround exists. `lumen-run --root` doesn't generalize (hard-checks
`hostname = lumen`, local transient units only, per wq#415). No allow-listed
remote-privileged-exec tool exists in `worker-settings.json` today.

## What's in this PR

`ops/wq358-sia-n100-efficiency-canary/`:
- `verify-sia.sh` — read-only, no-privilege status snapshot (RAPL/EPP/swappiness/unit
  state/RTL8125 NIC link+error counters on enp1s0/protected-PCI power-control/docker ps
  for the containerized PG17+miniflux+news-intake stack). Written and adapted from
  `verify-sobek.sh`; **not executed** — this session has no ssh reach to Sia at all, so
  unlike wq415 this could not be run for real. Whoever runs `deploy-sia.sh` should run
  this first to get a real pre-install snapshot.
- `deploy-sia.sh` — rsync pack + `ssh sia sudo bash install.sh` + verify + soak
  checklist, including the ASPM-canary-specific watch (NIC carrier changes / rx-tx
  errors / drops must stay flat) and the containerized-services check the ticket calls
  out (news-intake / miniflux / PG17 via docker, not systemd). Not executed, and not
  even `bash -n` syntax-checked — this session's allowlist only permits `bash scripts/*`,
  not an arbitrary path, so this needs root + ssh reach *and* a syntax check before
  first real use.
- `baseline-sia-pre.md` — **not a live capture** (unlike Sobek's). Transcribes the
  baseline already recorded in the wq#358 ticket text itself (PL1=10W, PL2=25W,
  EPP=balance_performance, swappiness=60, RTL8125 on enp1s0). Flagged explicitly as
  stale/ticket-sourced, not measured, with instructions to re-run `verify-sia.sh` for
  real once someone has ssh+root reach.

No changes to `/home/lumen/n100-efficiency/` itself.

## Open gap this attempt surfaced: Bes soak closure isn't documented

The ticket's gate is "wq#357 Bes canary soaks 48h+ clean (PG standby lag=0, HA/mosquitto
stable, no NIC drops)". wq#357's own ticket notes only cover staging + triggering the
canary install (`claimed_at`/`done_at` three minutes apart, 2026-07-17) — there is no
soak-close report in this repo (`grep -rl "soak" reports/*.md | xargs grep -l -i bes`
returns no Bes-soak-verification file) confirming the 48h window actually completed
clean. Over a month has elapsed since, so it's plausible, but this session found no
positive evidence to cite — flagged in `baseline-sia-pre.md` as a pre-req to check
before anyone runs `deploy-sia.sh`.

## Recommendation

Same as wq#415: either (a) Frank or a claude-web session confirms the Bes soak closed
clean, then runs `verify-sia.sh` for a real baseline and `deploy-sia.sh` to install, or
(b) a scoped, reviewed remote-privileged-exec allowance gets added for BUILD-lane
sessions if fleet installs are meant to be a closeable BUILD-lane deliverable going
forward — worth escalating once, not re-discovering per-ticket (this is now the 3rd
ticket — wq#358, wq#415, and wq#357's own canary step — to dead-end on the identical
missing capability).

## Rollback
`ssh sia 'sudo bash ~/n100-efficiency/uninstall.sh'` (same design as wq#357/#415:
snapshots restore, unit disabled, PCI/USB/i915 revert on reboot).
