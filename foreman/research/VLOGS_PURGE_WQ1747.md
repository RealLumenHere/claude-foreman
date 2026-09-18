# wq#1969 — VictoriaLogs (Bes) purge/expire path for wq#1747's leaked secrets

**Scope: research only. No changes made to Bes, VictoriaLogs, or any fleet node this
session.** All findings below came from read-only `curl` against Bes's already-exposed
VictoriaLogs HTTP API (`http://100.110.156.91:9428/*`, no auth required — see Finding 0)
and from `docs.victoriametrics.com` / the VictoriaLogs CHANGELOG, fetched live this
session (2026-09-18). No `ssh` command to Bes was run or was needed for this research —
the read-only HTTP surface already answered every question in the ticket. `ssh
bes@...` itself is not on this worker's Bash allowlist (`This command requires
approval`, unretried per doctrine); the SSH-wrapped commands below are written for
whoever executes the recommendation, using the `bes@100.110.156.91` target confirmed
live in `bin/vw-bes-datasync.sh:6`.

## RECOMMENDATION

**Upgrade the existing single Bes instance in place to a current VictoriaLogs release,
enable the delete API behind an auth key for the duration of one scoped delete task,
issue a single precise `LogsQL`-filtered `POST /delete/run_task`, verify, then remove the
delete flags again.** Do this instead of either alternative the ticket floated (snapshot
+ full re-ingest, or a second short-retention instance) — see "Why not the other two
options" below. Rotating the leaked LiteLLM virtual keys and the two vendor API keys is
wq#1747's actual remediation and is unaffected by this ticket; purging the rows only
removes the exposure inside VictoriaLogs.

## Finding 0 — current state on Bes (confirmed live this session)

- `curl http://100.110.156.91:9428/flags` → `-retentionPeriod="90d"`,
  `-storageDataPath="/victoria-logs-data"`, `-httpListenAddr=":9428"`, plus two syslog
  listeners. **No `-httpAuth.*`, `-delete.*`, or `-forceMergeAuthKey` flags are set** —
  the instance is completely unauthenticated on the tailnet today; every command below
  that would add `-delete.enable` must add `-deleteAuthKey` in the same step, or any
  tailnet peer gets an unauthenticated log-delete endpoint.
- `curl .../metrics | grep app_version` → `victoria-logs-...-tags-v1.19.0-...`, matching
  the ticket's stated version exactly. Build timestamp `20250417-182045` matches the
  CHANGELOG's `v1.19.0` release date (`2025-04-17` — see Finding 1).
- Single-node deployment (flags match the single-binary `victoria-logs`, not
  `vlselect`/`vlstorage` cluster flags) — the cluster-only update notes in Finding 3
  don't apply here.
- Dataset is tiny: `* | count()` → **17,095,006 rows**; `/metrics` →
  `vl_data_size_bytes{type="storage"}=262468513` (**~250 MiB**),
  `vl_free_disk_space_bytes=450657259520` (**~420 GiB free**). Any of the remediation
  paths below costs seconds of I/O, not the "terabytes... rewrites all stored logs, not
  recommended frequently" case the docs warn about for the delete API.
- `syslog_id:supergateway` alone matches **938,284 rows**, not the 1,061 the ticket
  cites — the leak is a small subset of the supergateway stream, identified by
  message-body content (the JSON-RPC bodies carrying the LiteLLM/vendor keys), not by
  stream or unit alone. Whoever runs the delete step must re-derive wq#1747's exact
  match (time range + body pattern) and dry-run it as a plain read query first —
  confirm the hit count is exactly 1,061 — before calling `/delete/run_task` with it
  (see Steps, step 5).
- `curl .../select/logsql/streams -d 'query=*'` → **every row currently lands in the
  same default stream, `_stream="{}"`** (17,093,062 of the 17,095,006 hits), plus one
  unrelated `test-deploy` stream. The `vlogs-forwarder`/journald pipeline doesn't tag
  ingested rows with a per-unit stream field. This matters for Finding 2 and for why
  the "second instance, one stream only" option doesn't work as stated today.

## Finding 1 — no deletion path exists on v1.19.0; delete API landed in v1.38.0

Searched the full VictoriaLogs CHANGELOG (`docs.victoriametrics.com/victorialogs/changelog/`,
every version header from `v0.1.0` through `tip`, fetched live). Bes's `v1.19.0` released
**2025-04-17**. The delete API's first appearance:

> **v1.38.0**, released **2025-11-14**: "FEATURE: add an ability to delete stored logs."
> (CHANGELOG, credited to community contribution at #4/#43.)

That's **19 minor versions after** what Bes runs — nothing in v1.19.0 or earlier can
delete, expire-early, or filter-retain a subset of ingested logs. The mechanism, from
the VictoriaLogs docs ("How to delete logs" section, `docs.victoriametrics.com/victorialogs/#how-to-delete-logs`):

- Disabled by default. Requires the `-delete.enable` command-line flag.
- `POST /delete/run_task?filter=<LogsQL_filter>` — starts an **async** task that
  rewrites all stored blocks matching an arbitrary LogsQL filter. Not a per-row delete
  (the docs are explicit: "isn't recommended on a frequent basis... intended for rare
  exceptional cases such as GDPR compliance or removal of accidentally written
  security-sensitive data" — this is that exact case), but the LogsQL filter itself can
  be as narrow as a specific field/substring/time-range match, so it can be scoped to
  exactly the leaked rows.
- Returns `{"task_id":"<id>"}`; track/cancel via `GET /delete/active_tasks` and
  `POST /delete/stop_task?task_id=<id>`. Deleted rows remain visible in queries until the
  task completes.
- `-deleteAuthKey` (protects `/delete/*` independently of `-httpAuth.*`) and enforcing
  `POST`-only on `/delete/run_task` and the related `/internal/force_merge`,
  `/internal/force_flush`, `/internal/log_new_streams`, `/internal/partition/*`
  endpoints (closing a GET-based SSRF class, GitHub #1635) are both present in the
  CHANGELOG's newest entries, which sit **after** the latest numbered release the
  CHANGELOG lists (`v1.52.0`, released 2026-07-16) — i.e. they ship in whatever release
  follows v1.52.0. **Upgrade to the current stable at execution time, not the bare
  minimum v1.38.0** — deploying `-delete.enable` without the auth-key/POST-only
  hardening on an instance that already has zero auth (Finding 0) would be a net-new
  exposure, not a fix.
- Cluster mode needs `-delete.enable` on `vlselect` and `-internaldelete.enable` on
  `vlstorage` — not applicable here, Bes runs single-node (Finding 0).

## Finding 2 — no per-stream retention filters exist in VictoriaLogs at any version

Searched the CHANGELOG for `retention filter`, `retentionFilter`, `per-stream retention`
— **zero matches at any version**, `v0.1.0` through `tip`. The only retention controls
VictoriaLogs has, ever, are global:

- `-retentionPeriod` (time-based; this is the `90d` Bes has set today).
- `-retention.maxDiskSpaceUsageBytes` / `-retention.maxDiskUsagePercent` (disk-usage
  based, drops oldest per-day partitions once a size threshold is hit — introduced
  before v1.19.0, unrelated to which *stream* the data belongs to).

Neither can single out one stream, service, or unit. VictoriaMetrics Enterprise has a
`retentionFilters` feature for metrics; **VictoriaLogs has no equivalent, in OSS or
enterprise, as of the newest CHANGELOG entry seen this session.** Even if it existed,
Finding 0 shows every current row shares one stream (`_stream="{}"`) — a per-stream
retention rule would need an ingestion-side change (stream fields on the forwarder) to
have anything to key off of. Ruled out as an option for this ticket.

## Finding 3 — forced merge does not delete anything and takes no stream filter

Checked the "Forced merge" section of the docs directly (the ticket explicitly asked
about this option). `POST /internal/force_merge?partition_prefix=YYYYMMDD` triggers a
background **compaction** of one whole per-day partition, for query performance — it
takes a date, not a stream/field filter, and "It is unnecessary... under normal
conditions" — it does not remove or expire any data. Ruled out entirely; this endpoint
cannot be used for purging.

Also checked storage-format compatibility for the planned upgrade: the CHANGELOG's two
"changes data storage format in a backwards-incompatible way" update notes are dated
**2025-02-19** and **2025-03-16**, both **before** v1.19.0 (2025-04-17). No further
format-breaking update note appears between v1.19.0 and v1.52.0. A direct upgrade from
v1.19.0 to current is safe on that front; the only breaking *behavioral* notes in that
range are cluster-only (`vlselect`/`vlstorage` protocol — N/A, single-node) or
LogsQL-syntax strictness for a small set of legacy unprefixed-filter queries (v1.51.0)
that don't affect ingestion or the recommended delete filter.

## Why not the other two options the ticket floated

- **Snapshot + selective full re-ingest**: would mean exporting all ~17M rows / 90 days,
  filtering client-side, and re-inserting everything minus the 1,061 rows, then swapping
  storage paths — the largest blast radius of any option, for a problem that's actually
  1,061 rows out of 17 million. No upside over a scoped delete once the delete API is
  available, and it's the option most likely to silently drop or duplicate unrelated
  rows during re-ingest.
- **Second VictoriaLogs instance, short retention, receiving only the supergateway
  stream**: doesn't do what it needs to as stated, because of Finding 0 — there is no
  isolated "supergateway stream" today, everything ingests into the same default stream.
  Standing up a second instance would first require changing the `vlogs-forwarder`
  ingestion path to tag by unit (a separate, larger change, valuable as future hygiene
  but out of scope here), and even then it only stops *future* supergateway logs from
  landing in the 90-day instance — the 1,061 rows already sitting in Bes's current
  instance would still ride out the full 90-day `-retentionPeriod` untouched. It doesn't
  purge anything already ingested, which is the actual ask.
- **"Upgrade to a version with retention filters"**: no such version exists (Finding 2).

The upgrade + one scoped delete is the only option that actually removes the
already-ingested secrets, matches the docs' explicitly endorsed use case for this exact
feature, and costs nothing given the ~250 MiB dataset size.

## Steps (SSH-wrapped for Bes; none of this was executed this session — recommendation only)

1. Snapshot every per-day partition covering the leak window plus margin, for rollback:
   `ssh bes@100.110.156.91 'for d in <YYYYMMDD...>; do curl -s -X POST "http://127.0.0.1:9428/internal/partition/snapshot/create?partition_prefix=$d"; done'`
2. Back the snapshot(s) up off-box before touching anything:
   `ssh bes@100.110.156.91 'rsync -avh --delete <snapshot-path>/ <backup-host>:<backup-path>/'`
3. Stop the `victorialogs` unit, install the current VictoriaLogs release (checksum-verified,
   same pattern as `ops/wq857-victorialogs-bes/install-victorialogs.sh`), add
   `-delete.enable` and `-deleteAuthKey=<newly generated secret>` to the unit, restart:
   `ssh bes@100.110.156.91 'sudo systemctl stop victorialogs && ...install... && sudo systemctl start victorialogs'`
   (root steps here need the `fleet-exec` broker or an operator, per the `wq#857`/`wq#879`
   policy already on record for this exact host — a BUILD-lane agent doesn't ride a guard
   hole to do this itself.)
4. Verify: `ssh bes@100.110.156.91 'curl -s http://127.0.0.1:9428/flags'` shows
   `-delete.enable` and the new version; retention and row count roughly unchanged.
5. Re-derive wq#1747's exact leaked-row filter (time range + body-content match — not
   `syslog_id:supergateway` alone, see Finding 0) and dry-run it as a **read** query
   first: `ssh bes@100.110.156.91 'curl -s "http://127.0.0.1:9428/select/logsql/query" --data-urlencode "query=<filter> | count()"'`
   — confirm it returns exactly 1,061 before proceeding.
6. Purge: `ssh bes@100.110.156.91 'curl -s -X POST "http://127.0.0.1:9428/delete/run_task?filter=<url-encoded filter>&authKey=<key>"'`
   — capture `task_id`.
7. Poll until done: `ssh bes@100.110.156.91 'curl -s "http://127.0.0.1:9428/delete/active_tasks?authKey=<key>"'`
   until the `task_id` no longer appears.
8. Verify purge: re-run step 5's read query — expect `0`.
9. Remove `-delete.enable`/`-deleteAuthKey` from the unit and restart, restoring today's
   flag set (minus the version bump) — don't leave a delete-capable endpoint sitting on
   the tailnet indefinitely.
10. Rotate the leaked LiteLLM virtual keys and the two vendor API keys (wq#1747's actual
    fix) — independent of this ticket, but the reason it matters.

## Risk

- Delete is async and rewrites matching blocks; for ~250 MiB this is seconds, not the
  docs' "terabytes" caveat.
- The LogsQL filter must be exact — a wrong filter deletes more than intended, and it's
  irreversible once the task completes except by restoring the pre-delete partition
  snapshot from step 1. Step 5's dry-run-as-count-query is mandatory, not optional.
- The instance has zero auth today (Finding 0); `-delete.enable` must never be added
  without `-deleteAuthKey` in the same change.
- Jumping 19 minor versions in one hop: no intervening storage-format break exists
  (Finding 3), so this is expected to be safe, but this session did not test it — the
  snapshot in step 1 is the actual safety net, not the changelog read.
- Brief ingestion gap while the unit restarts (steps 3 and 9). `vlogs-forwarder`'s own
  retry/buffering behavior on a failed push was not verified this session — the
  operator executing this should confirm `forward-journal.sh`'s retry semantics before
  scheduling the restart, to bound how much new data (not the leaked rows — new,
  unrelated logs) could be lost during the restart window.

## Rollback

- Upgrade misbehaves: stop `victorialogs`, restore the prior binary, restore
  `-storageDataPath` from the step-1 snapshot via
  `/internal/partition/detach?name=YYYYMMDD` → `rsync` the snapshot back →
  `/internal/partition/attach?name=YYYYMMDD` (VictoriaLogs backup/restore docs), restart
  on the old binary and old flags.
- Delete task removes more than intended: same detach/rsync/attach flow, restoring only
  the affected `YYYYMMDD` partition(s) from the step-1 snapshot — accepting the loss of
  any genuinely new, unrelated rows ingested into that partition between the snapshot
  and the restore.
