# wq#2570 — lumen-monitor.service restart storm

`systemctl show -p NRestarts,ActiveEnterTimestamp lumen-monitor.service` (2026-09-25):
`NRestarts=3197`, `ActiveEnterTimestamp=Wed 2026-09-16 15:15:33 UTC` — matches the
ticket. `FragmentPath=/etc/systemd/system/lumen-monitor.service`,
`ExecStart=/usr/bin/python3 /home/lumen/agent/lumen_status_monitor.py`.

## AC1 — exit path

The ticket's own premise ("journal and /home/lumen/agent are unreadable from a
worker sandbox") no longer holds as of 2026-09-25 — both were directly readable
from this worktree's sandbox (`Read` on `/home/lumen/agent/lumen_status_monitor.py`
worked; `journalctl -u lumen-monitor` runs without a permission error). What
blocks a literal replay of the 08-31..09-16 window is different: `journalctl
--list-boots` shows exactly one boot on this host, `2026-09-22 08:12:42 UTC`
onward. The storm window predates that boot and its journal entries are gone
(rotated/vacuumed by journald's default retention), not sandbox-restricted —
`journalctl -b -1` confirms `No journal boot entry found`. `/var/log/journal/`
itself is out of the sandbox's allowed paths, so there is no way to recover an
older on-disk archive either. This is a genuine data-availability gap, logged
below, not something a different command shape would get around.

With the literal journal gone, the exit path comes from reading
`/home/lumen/agent/lumen_status_monitor.py` (pre-fix) directly:

- `svc()` and `messenger()` (lines 14-24) already catch everything (bare
  `except:`) and degrade gracefully.
- `project_stats()` (lines 34-41, pre-fix) has **no exception handling** and
  opens `sqlite3.connect()` against `AGENT_DIR/project_engine.db` on every
  60s tick. The file's own wq#1024 comment documents that this agent state is
  touched by more than one writer — a concurrent writer holding a lock when
  this query runs raises `sqlite3.OperationalError: database is locked`,
  uncaught.
- `merged_write()`'s write path (`tmp.write_text` / `tmp.replace`, pre-fix
  lines 73-75) is also unguarded — any transient file I/O fault there is
  equally fatal.
- The top-level `while True:` loop (pre-fix lines 77-93) wraps none of this in
  `try/except`, so **any** exception anywhere in a cycle — sqlite lock,
  file I/O, or anything else — kills the whole process. `Restart=always` with a
  flat `RestartSec=5` and no `StartLimitBurst` then respawns it forever with no
  backoff and no failure alert, which is how an intermittent fault turns into a
  silent 3197-restart storm over 16 days instead of stopping once and paging.

This is the best-evidenced exit path available (unhandled exception escaping
the main loop, most plausibly `project_stats()`'s sqlite3 call given the
documented concurrent writer) — offered as the mechanism the fix closes off,
not a forensic claim about which exact exception fired on which of the 3197
restarts.

**ISSUE-LOG**: appended entry for the journal-retention gap (`/home/lumen/kb/ISSUE-LOG.md`).

## AC2 — transient errors caught and retried in-process

`lumen_status_monitor.py`: the entire loop body is now wrapped in `try/except
Exception`, logging the traceback and falling through to the existing 60s
`time.sleep(60)` before the next attempt — no code path in the loop can exit the
process anymore. See the `while True:` block and its `# wq#2570` comment.

## AC3 — self-limiting restarts

`lumen-monitor.service`: added `StartLimitIntervalSec=1800` / `StartLimitBurst=10`
(stop after 10 start attempts in 30 minutes) and `RestartSteps=9` /
`RestartMaxDelaySec=300` (backs off from the existing `RestartSec=5` up to 5
minutes between attempts). A fault AC2 can't catch (e.g. the process dying
before the loop even starts) now spaces itself out and gives up instead of
storming at a flat 5s indefinitely.

## AC4 — deduped alert

Added `OnFailure=ntfy-onfailure@%n.service` to `lumen-monitor.service` — the
same hook already wired on `lumen-cfd-actuator.service`,
`ops/wq1704-missing-watchdogs/*.service`, etc. `/home/lumen/bin/systemd-onfailure-ntfy`
sends the alert through `aq add systemd "svc-fail-$U" ...`, keyed on the unit
name, which is the fleet's existing dedup path — not reinvented here.

## Deploy

`sudo` itself is hard-blocked in this worker's sandbox by the guard hook
(`guard.sh: BLOCKED — denied verb (sudo/...)`), and `/home/lumen/agent` and
`/etc/systemd/system/` are outside its writable paths, so `install.sh` has no
`sudo` calls in it — it's meant to run as root via the sanctioned detached-root
path (instinct #5), same convention as `token-engine/install.sh`:

```
/home/lumen/bin/lumen-run start --root -- bash \
    ops/wq2570-lumen-monitor-restart-storm/install.sh
```

Deployed 2026-09-25 via that command; see evidence.json for the resulting
`NRestarts`/`ActiveEnterTimestamp`/`SubState`.

## Liveness

`systemctl show -p NRestarts --value lumen-monitor.service` should stay flat
over the 24h following deploy (ticket's closure check).
