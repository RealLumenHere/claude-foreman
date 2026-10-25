## What

Research + a small reference implementation for wq#4629: why Commander
drops long-running builds, what the fleet already ships to survive that,
and the one gap that's missing — nothing notifies on completion.

- `research/wq4629_commander_watchdog_survival.md` — the writeup.
- `ops/wq4629-commander-watchdog-a1/` — `lumen-job-notify.sh` (bridges a
  `lumen-run` unit's terminal state into a filesystem marker),
  `commander-job-notify.path`/`.service` (a `systemd.path`/inotify oneshot
  drain modeled on `research/systemd/lumen-research-sandbox.path`),
  `drain_done_dir.sh` (ntfy-send + optional `claude -p` injection, clears
  the marker), and a pytest suite.

## Why

The ticket asked for watchdog patterns so a 30+ minute `build-limpet.sh`
survives a Commander call without the agent wrongly reporting failure. The
research found the kill is not a flat 90s timeout but a Cloudflare Worker's
70s `tools/call` abort cascading into a process-group SIGTERM
(`reports/wq3264_mcpw_commander_drops_rca.md:9-29`), and that survival is
already solved twice (`ops/wq3103-commander-guard`'s clamp+detach,
`/home/lumen/bin/lumen-run`'s `systemd-run` unit tracking). What's missing
is notification: every existing tool is pull-only, so an agent or human has
to remember to come back and ask whether the job finished.

## How

`lumen-job-notify.sh` is launched detached and loops `lumen-run wait UNIT`
past `TIMEOUT` results to a terminal state, then writes a JSON marker into
a done-dir. A `systemd.path` unit (inotify under the hood) watches that
dir and wakes a oneshot `drain_done_dir.sh`, which `ntfy-send`s the result,
optionally spawns a detached `claude -p "<result>"` (the one real way to
get an async result in front of a fresh Claude turn, since nothing can push
into an already-running one), and deletes the marker so the `.path` unit
re-arms. The `.path`/`.service` pair is shipped as a tested proposal, not
installed — this sandbox has no root/privilege-escalation verb for
`/etc/systemd/system` + `daemon-reload` (same pattern as
`ops/wq2606-mcp-gateway-watchdog-a1`).

## Testing

`pytest ops/wq4629-commander-watchdog-a1/test_commander_watchdog.py` — 2
passed. Stubs `lumen-run`/`ntfy-send`/`claude` so both scripts are
exercised end-to-end (re-poll past a `TIMEOUT`, write the marker, notify,
inject via `claude -p`, clear the marker) with no real systemd unit, no
network call, and no fleet credential.

## Risk/rollback

Docs + two new, additive scripts and two uninstalled systemd unit files
under a new `ops/` directory. Nothing existing is modified —
`lumen-run`, `ops/wq3103-commander-guard`, and every live systemd unit are
untouched. Rollback is deleting the new files; nothing is deployed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
