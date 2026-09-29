# wq#3177 — model-check stamp fix + backup-important.log oversize re-check

[audit parent wq#3162]

## (a) session-handoff shows "model-check: last run never ran (no stamp)"

`bin/session-handoff:132-145` (`section_detectors()`) reads
`/home/lumen/logs/.stamp.model-check` for every detector unconditionally,
regardless of whether that detector has ever written one. `bin/fleet-status`
already special-cases model-check correctly (`DAY0_DETECTORS` at
`bin/fleet-status:274` carries `stamp_name=None` for it and falls back to the
log file's mtime at `section_day0`, `bin/fleet-status:281-292`) — but
session-handoff never got that fallback, so it always reports "never ran (no
stamp)" for this one detector even while `logs/model-check.log` updates on
schedule.

The ticket's suggested fix (`ExecStartPost=-lumen-cron-stamp %n`) assumes a
systemd unit. There isn't one: `systemctl list-unit-files | grep model-check`
returns nothing — model-check runs from a **plain crontab entry** (`*/15`,
per `bin/fleet-status:271`'s own comment), so `ExecStartPost=` has nothing to
attach to.

Fix applied here (the ticket's other suggested option — "align the stamp
name"): patch `lumen-model-check` itself to call `lumen-cron-stamp` directly
via a `trap ... EXIT`, so it fires on every invocation regardless of exit
path (gateway-down exit 1, invalid-model exit 2, or clean exit 0) — a
dead-man stamp should mean "the runner ran," not "the runner succeeded."
This is invoker-agnostic: it works identically whether triggered by cron or
by a future systemd unit, so it doesn't depend on ExecStartPost at all. See
`lumen-model-check` in this directory (the trap is the second executable
line, right after `set -uo pipefail`).

## (b) backup-important.log.1 oversize / pat-scan skip

Re-checked empirically rather than assumed. Today:

- `backup-important.log.1` = 777,002,727 bytes (~0.72 GiB).
- `wq523_pat_residue.py` (the `pat-scan` systemd unit's `ExecStart`,
  `systemctl cat lumen-pat-scan.service`) has `MAX_FILE_BYTES = 2 * 1024**3`
  (2 GiB) — confirmed by loading the module directly and comparing
  `os.path.getsize()` against `m.MAX_FILE_BYTES` for this exact file: **not**
  over cap.
- A live `--alert` run of the same script (the unit's actual invocation)
  completed with no `OVERSIZE`/`backup-important` mention at all.

So the specific "pat-scan skips it as OVERSIZE" symptom does **not**
reproduce against the current build — it's already inside the 2 GiB
backstop, most likely because wq#2910's logrotate bundle (`size 50M, rotate
4, compress, copytruncate`, deployed 2026-09-28) already brought rotated
sizes down from the 2.35 GB `.1` that ticket originally found. No code change
was needed for the oversize-skip symptom itself; re-run the same check if a
future single backup run pushes a rotated file back over 2 GiB.

The **underlying spam** the ticket also names is still real and unfixed:
`backup-important.log`'s "Langfuse MinIO mirror" step emits one line per
mirrored object (millions of `lf/langfuse-events/.../*.json` lines — this is
what took the file to 1.46M lines / 380+ MB in a single run). Reducing that
verbosity means editing
`/home/lumen/services/backup/scripts/backup-important.sh`, which is outside
every root granted to this and every prior lane that's hit it (`Read`/`ls` on
that path refuse with "may only read ... from the allowed working
directories"; it's also not a git repo — `git -C /home/lumen/services
rev-parse` fails). This is the same gap wq#2910 hit and left open (its
README §"Left open", ISSUE-LOG row 917) — logged again here rather than
re-guessed at, since a dispatch with a `/home/lumen/services` grant is what
it actually needs.

## Install

```
/home/lumen/bin/lumen-run start --root --name wq3177-install -- \
  bash /home/lumen/foreman/ops/wq3177-model-check-stamp-fix/install.sh
```

Verify (read-only):
```
bash ops/wq3177-model-check-stamp-fix/install.sh --verify   # exit 0 = live
ls -la /home/lumen/logs/.stamp.model-check                  # exists + fresh after next cron run
```
