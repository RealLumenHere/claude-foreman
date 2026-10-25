# wq#4629 — Commander long-command survival: reference watchdog bundle

Companion code for `research/wq4629_commander_watchdog_survival.md`. Fills the
one gap that research found in the fleet's existing long-job tooling
(`/home/lumen/bin/lumen-run`): nothing notifies on completion. Everything here
is additive — it does not modify `lumen-run`, `desktop-commander-mcp`, or any
already-deployed unit.

## Pieces

- **`lumen-job-notify.sh`** — launch detached (never through Commander: it
  blocks for the job's full runtime). Polls `lumen-run wait UNIT` to a
  terminal state and writes a completion marker JSON into a done-dir. This is
  the "active watcher" half: it turns a systemd-unit's state into a
  filesystem event, since `lumen-run`'s unit-based jobs have no native
  "I'm done" signal a `.path` unit could watch directly.
- **`commander-job-notify.path` / `.service`** — the "passive trigger" half:
  a `systemd.path` unit (inotify under the hood — confirmed against
  `research/systemd/lumen-research-sandbox.path`, the fleet's existing
  `DirectoryNotEmpty=` template) wakes a oneshot `drain_done_dir.sh` the
  instant a marker appears. Zero CPU while idle.
- **`drain_done_dir.sh`** — the oneshot body: `ntfy-send`s the result,
  optionally spawns a detached `claude -p "<result>"` session (the one real
  way to get an async result in front of a fresh Claude turn — nothing can
  push into an already-running one), then deletes the marker so the
  directory empties and the `.path` unit re-arms.
- **`test_commander_watchdog.py`** — stubs `lumen-run`/`ntfy-send`/`claude`
  so both scripts are exercised end-to-end with no real systemd unit, no
  network call, and no fleet credential. `pytest ops/wq4629-commander-watchdog-a1/test_commander_watchdog.py`.

## Why two halves instead of one script that does everything

A single script that polls-then-notifies works (and is simpler), but it is
one more long-lived process per in-flight build, sleeping in a loop. Splitting
the "detect completion" step (`lumen-job-notify.sh`, one per job, short-lived
relative to the job) from the "react to completion" step (the `.path` unit,
one per host, always-on but inotify-blocked rather than polling) means the
*reaction* side costs nothing while idle and never duplicates a notification
even if several jobs finish at once — `drain_done_dir.sh` drains every
marker it finds in one wake.

## Deploying (not done here — root-owned paths, per ISSUE-LOG wq#2606 pattern)

This BUILD sandbox cannot write `/etc/systemd/system/*` or run
`systemctl daemon-reload` (both root-only, no privilege-escalation verb
granted). To go live:

```
install -D commander-job-notify.path commander-job-notify.service \
  -t /etc/systemd/system/
mkdir -p /home/lumen/var/commander-watchdog/done   # as lumen, before enabling
systemctl daemon-reload
systemctl enable --now commander-job-notify.path
```

Then launch a job with notification:

```
UNIT=$(/home/lumen/bin/lumen-run start --name build-limpet -- /home/lumen/path/to/build-limpet.sh | sed -n 's/^UNIT=//p')
/home/lumen/bin/lumen-run start --name notify-"$UNIT" -- \
  /home/lumen/foreman/ops/wq4629-commander-watchdog-a1/lumen-job-notify.sh "$UNIT"
```

`lumen-job-notify.sh` itself is lumen-owned and runs today, with no root
step, standalone — only the `.path`/`.service` pair needs the authorized
deploy. See the research doc's "what's actually new here" section for the
honest scope of this ticket.
