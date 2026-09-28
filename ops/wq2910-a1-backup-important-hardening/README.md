# wq#2910 — lumen-backup-important failed 09-27, no error text in the log

fleet-audit-2 (2026-09-28) found `lumen-backup-important.service` exit 2 on
2026-09-27 11:03Z: `/home/lumen/logs/backup-important.log` shows

```
[2026-09-27T11:03:58Z] === Langfuse ClickHouse dump ===
[2026-09-27T11:03:58Z]   FAILED dump traces
[2026-09-27T11:03:58Z]   FAILED dump observations
[2026-09-27T11:03:58Z]   FAILED dump scores
[2026-09-27T11:03:58Z] === Langfuse MinIO mirror ===
[2026-09-27T11:03:59Z]   FAILED MinIO mirror (continuing)
```

with zero stderr text for any of the four failures (verified by reading the
log directly — this lane has `/home/lumen/logs` as a granted root).

## What could not be fixed from this lane, and why

The ticket's fix #1 (capture each dump step's stderr into the log) means
editing `/home/lumen/services/backup/scripts/backup-important.sh`. That path
is outside every root granted to this worktree (worktree +
`agents/work-queue`, `mailbot`, `pokemon`, `bin`, `foreman`, `kb`,
`projects/nest`, `logs`, `/tmp/fworker-wq2910`) — `Read`/`ls`/`stat`/`cat` on
it are all refused ("may only read ... from the allowed working
directories"). It is also not a git repo (`git -C /home/lumen/services
rev-parse` → "not a git repository") and not tracked anywhere in
`claude-foreman` (`git log -- services/backup` is empty), so there is no
readable copy anywhere in this lane's reach to patch from. This is the same
class of gap ISSUE-LOG row 298 hit for the sibling `backup-critical.sh`
(wq#1704) — logged again as row 917 for this ticket, since it's a distinct
occurrence against a different script.

One thing *was* checkable without reading the script: `systemctl cat
lumen-backup-important.service` (a systemd query, not a raw path read, so
it isn't grant-gated) shows the `50-quiet-journal.conf` drop-in (wq#2052)
already sets

```
StandardOutput=append:/home/lumen/logs/backup-important.log
StandardError=append:/home/lumen/logs/backup-important.log
```

i.e. the unit's own stdout *and* stderr already land in the one log this
lane can read. Since the dump-step failures still show up with no error
text, each step's subprocess stderr must be getting redirected away
*inside* the script (the classic `cmd 2>/dev/null` / `cmd &>/dev/null`
swallow pattern) before it would ever reach the unit's own streams — no
systemd-level override can recover output the script itself discards.
**The stderr-capture fix has to be a source edit.**

### The exact patch needed (for whoever has a `/home/lumen/services` grant)

Wrap each of the four steps so the real stderr lands in `$LOG` instead of
being discarded, per the ticket's own suggested approach:

```sh
# before (reconstructed shape — swallows stderr, only echoes "FAILED X"):
if dump_traces ... ; then
  log "  dumped traces"
else
  log "  FAILED dump traces"
fi

# after:
if dump_traces ... 2>&1 | tee -a "$LOG" | tail -n 20; then
  log "  dumped traces"
else
  log "  FAILED dump traces (see above)"
fi
```

Apply the same wrap to the `observations`, `scores`, and MinIO-mirror steps.
Note `env.sh` also points at a *second*, script-local log directory
(`LOG_DIR` under `/home/lumen/services/backup/logs/`, per the
`50-quiet-journal.conf` comment) — check whether that one already has the
real stderr before assuming it needs the same patch.

### Probable root cause — needs confirmation, not assumed

wq#2756 (2026-09-26, the day before this failure) rotated the live Langfuse
project API key (`pk-lf-lumen-2026`) end-to-end. If the ClickHouse dump or
MinIO mirror step authenticates through that key (directly, or via a
Langfuse export API rather than a raw `clickhouse-client`/`mc` credential),
the 2026-09-27 11:03Z run is the first run after the rotation and would be
exactly the failure mode here. This is the ticket's own probable-cause note,
and it lines up on timing, but it is **not verified** — confirming it needs
the same script + `env.sh` read access as the patch above.

## What was fixed here (fully reachable, no guessing required)

1. **Log rotation** — `backup-important-logs.logrotate`: `/home/lumen/logs/backup-important.log`
   at size 50M, rotate 4, `copytruncate` (the log's fd is opened by systemd
   as root before the unit drops to `User=lumen`, so the file must keep its
   inode across a run rather than being renamed out from under that fd).
   `.log.1` was observed at 2.35GB before this fix (`ls -la
   /home/lumen/logs/backup-important.log*`).

2. **"not just a red unit nobody lists"** — `90-onfailure-alert.conf`: a
   `lumen-backup-important.service.d` drop-in adding
   `OnFailure=ntfy-onfailure@%n.service`. That template
   (`systemctl cat ntfy-onfailure@.service` → `ExecStart=/home/lumen/bin/systemd-onfailure-ntfy %i`)
   is already wired on 7 other units in this repo
   (`grep -rn OnFailure ops/*/*.service`: wq1704's four spof watchers,
   wq2851, wq1652, wq901), and `systemd-onfailure-ntfy:33` already runs
   `/home/lumen/bin/aq add systemd "svc-fail-$U" "$U failed (result=$RES)" "$MSG"`
   on any unit it's attached to — reusing that template is a one-line
   `[Unit]` drop-in, not new code, and needed no access to the swallowed
   script at all. Any future failed `lumen-backup-important` run now opens
   an aq row and an ntfy alert, independent of item 1.

Item 3 (aq-on-failure) is therefore implemented via `OnFailure=` rather than
"from the script" as the ticket phrased it — same outcome (a failed run
always opens an aq row), and it also fires if the script itself crashes
before reaching its own alerting code, which a script-internal check
wouldn't.

## Install

```
lumen-run --root -- bash /home/lumen/foreman/ops/wq2910-a1-backup-important-hardening/install.sh
```

Verify: `logrotate -d /etc/logrotate.d/lumen-backup-important` (dry run),
`systemctl cat lumen-backup-important.service | grep OnFailure`. Rollback
command is printed by `install.sh` itself.

## Left open

Filed as ISSUE-LOG row 917: the stderr-capture patch and root-cause
confirmation need a follow-up ticket dispatched with
`/home/lumen/services` as a worktree or `--add-dir` grant (or a
docker/root-capable lane), pointed at
`/home/lumen/services/backup/scripts/backup-important.sh` and its `env.sh`.
