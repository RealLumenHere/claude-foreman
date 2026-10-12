# wq#3717 — backup-important red 4 days, stopped containers counted as ERRORS

`/home/lumen/services/backup/scripts/backup-important.sh` exits 3 daily since
wq#3672 Phase-1 subtraction stopped `plausible_db`, `plausible_events_db`,
`mongodb`: their dump legs kept running `docker exec` against now-stopped
containers, counted the failure as an ERROR, and tripped the daily
OnFailure->ntfy alert even though the restic legs (the actual backup) kept
succeeding. Alarm fatigue, no real data loss.

This directory is a staged copy of the applied fix for review/audit — the
live files were edited directly (this build lane's Edit/Write tools can
reach `/home/lumen/services/`, unlike its Bash tool, which is sandboxed to
the worktree + `--add-dir` roots; same gap ISSUE-LOG #917/wq#2910 hit, but
on this lane Edit/Write were in reach even though Bash wasn't). A backup of
the pre-fix script is at
`/tmp/fworker-wq3717/backup-important.sh.orig-20261012_025224` (this lane's
tmp scratch, per the ticket's "back up the script before editing").

## What changed

1. **Skip, don't ERROR, a leg whose container is intentionally stopped**
   (`backup-important.sh:39-66` + the three call sites at `:113-126`,
   `:130-157`, `:161-177`). A new `RETIRED_CONTAINERS` list
   (`plausible_db plausible_events_db mongodb`) plus `container_running()` /
   `container_is_retired()` / `skip_or_error()` helpers: not-running AND on
   the list -> `log SKIP`, `ERRORS` untouched. Not-running and NOT on the
   list -> still `FAILED`/`ERRORS++` (an unexpected stop, e.g. a crash, must
   still page). Running-but-dump-fails is unchanged (`ERRORS++`).
   The formal record backing the list is
   `docs/fleet/retired_services.yaml` entry
   `important-tier-phase1-subtraction`.

2. **MongoDB credential moved out of the script**
   (`backup-important.sh:160-177`). The old inline
   `mongodump --username=... --password=...` (plaintext in the script, also
   visible to any `ps` on the host while the container's `sh -c` ran) is
   replaced with `source "$MONGO_CREDENTIALS_FILE"` (new file
   `/home/lumen/services/backup/.mongo-credentials`, mode 600, referenced
   from `env.sh` by path only — `env.sh` itself still carries no secret,
   per its own "DO NOT add secrets here" header) and the credentials are
   passed into `docker exec` as positional args (`_` idiom, matching the
   existing Langfuse ClickHouse leg) instead of interpolated into the
   command string, so they don't show up in container `ps` output either.

3. **Stable restic stage path** (`backup-important.sh:18-30`). `STAGE_IMP`
   was `$STAGE_DIR/important-$TS` (a new string every run) so restic's
   default same-`--paths` parent-snapshot lookup never found a parent and
   re-read/re-hashed every file every day. Changed to the stable
   `$STAGE_DIR/important`; the pre-run sweep and the `cleanup` EXIT trap
   already remove it between runs, so nothing about persistence changes —
   only the path *string* restic sees is now constant across runs.

## Not done (out of scope for this ticket)

Dropping the Langfuse ClickHouse + MinIO legs (35-39 min CPU / 13-19GB peak
daily): the ticket makes this conditional on wq#3672's retirement being
approved. Checked wq#3672 — status `done`, notes recommend "RETIRE via
30-day compose STOP window", but the containers (`langfuse-clickhouse`,
`langfuse-minio`, etc.) are still live; this is a recommendation packet, not
an executed stop. Leaving those two legs alone until that stop actually
happens — dropping them now would silently stop backing up a stack that is
still running.
