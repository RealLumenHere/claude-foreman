# wq#1621 — Langfuse MinIO unbounded growth: retention fix

Source: wq#1156 disk audit (root 51%->82% since 2026-08-09). Root cause isolated to the
`langfuse_minio_data` docker volume.

## Root cause, quantified (2026-09-19)

`mc stat local/langfuse-events`: **30 GiB, 2,367,922 objects**, ILM Disabled.
`mc stat local/langfuse-media`: 4.7 MiB, 232 objects — negligible, NOT the driver.

Cross-checked against ClickHouse `blob_storage_file_log` (the table Langfuse itself uses
to track every blob it writes to S3): 100% of its 2,380,180 rows have
`bucket_name = 'langfuse-events'` (entity_type observation/trace/score), date range
2026-05-18 through today. Zero rows reference `langfuse-media` — the 232 media objects
are written via a different code path this log table doesn't track, and their total size
(4.7 MiB) is two orders of magnitude below the events bucket.

**Conclusion: 100% of the unbounded growth is `langfuse-events`, not media.** The
ticket's original framing ("MinIO (trace media/attachments) is the actual driver") was
directionally right about MinIO but attributed it to the wrong bucket.

## Why the plan in DEPLOYMENT.md:149 doesn't work as written

DEPLOYMENT.md's own deploy-time note says "Langfuse v3 supports retention TTL via
project settings — set to 90d for general traces, 365d for evaluator outputs." Checked
against current Langfuse docs and a GitHub discussion (#13390, confirmed on OSS
v3.167.4): **the Project Settings -> Data Retention feature requires an Enterprise
license** (`LANGFUSE_EE_LICENSE_KEY`). We don't have one (no Enterprise licensing
anywhere in fleet state or agent/.env). This is why it was "never actioned" — the
documented mechanism isn't available on this deployment, not an oversight.

Langfuse's own self-hosting docs (Blob Storage Configuration, Scaling Deployments) give
the actual self-hosted-without-Enterprise path instead:
- Bucket lifecycle rule on the **events** bucket (their own recommended default: 30d).
  Explicitly safe: "the events bucket holds in-flight queue items only" (their wording,
  echoed independently in our own DEPLOYMENT.md:146).
- A matching ClickHouse TTL on `blob_storage_file_log` so the S3 write-log doesn't keep
  growing after the underlying objects expire.
- Explicit warning **against** a naive lifecycle rule on the **media** bucket: media
  objects are referenced by trace/observation rows, and deleting the S3 object without
  the (Enterprise-gated) coordinated delete leaves broken references in the UI. Since
  media is empty-for-practical-purposes (4.7 MiB, 232 objects) and not the growth
  driver, the right call is to leave it alone rather than accept that risk for near-zero
  disk benefit.

## What was NOT touched, and why

`traces` (467 MiB), `observations` (1.20 GiB), and `scores` (178 KiB) ClickHouse tables
— the actual trace/observation *content*, as opposed to the S3-write-log — were left
without a TTL. Setting one would reproduce a manual, unofficial version of the
Enterprise retention feature (which deletes consistently across ClickHouse + S3 +
Postgres references together). ClickHouse growth here is ~25MB/day per the ticket's own
estimate and is not the disk-drift cause. This is a data-retention POLICY decision
(how long Frank wants trace content queryable), not a disk-hygiene fix, and deserves an
explicit call rather than being bundled into ops cleanup. Left as an open question below.

## Implemented (2026-09-19)

1. `mc ilm rule add --expire-days 30 local/langfuse-events` — rule ID
   `dan3nhv6mogg00d3b76g`, confirmed Enabled via `mc ilm rule ls`.
2. `ALTER TABLE blob_storage_file_log MODIFY TTL toDateTime(created_at) + INTERVAL 30 DAY DELETE`
   on langfuse-clickhouse. NOTE: the literal syntax from Langfuse's docs
   (`created_at + INTERVAL 30 DAY DELETE`) is rejected by ClickHouse 24.3.3 with
   `BAD_TTL_EXPRESSION` because `created_at` is `DateTime64(3)` and TTL expressions must
   resolve to `DateTime`/`Date` — needs the `toDateTime()` cast shown above. Confirmed via
   `SHOW CREATE TABLE` before/after.
3. Media bucket: no lifecycle rule applied (see above).

## Verification limits (honest accounting)

- MinIO's ILM scanner runs on its own internal schedule (not immediate) — object count
  will not visibly drop today. ClickHouse TTL merges are also background, not immediate.
- Per the ticket's own item (4), volume growth needs re-measuring after ~1 week to
  confirm the class is fixed, not just the instance. Filed as wq#2053 with a
  not-before note, since this session cannot wait a week.
- Per the ticket's own item (3) ("verify TTL actually deletes MinIO objects, not just
  ClickHouse rows"): the MinIO ILM rule and the ClickHouse TTL are two independent
  mechanisms hitting two different data stores (S3 objects vs. the log table), not one
  feature verifying the other. Both are now confirmed *configured*; wq#2053's
  re-measurement is what confirms they're actually *firing*.

## Open decision for Frank (not blocking this ticket's close)

Whether to set an explicit ClickHouse TTL on `traces`/`observations`/`scores` content
itself (i.e., how long should trace *content* remain queryable in the Langfuse UI,
separate from the disposable S3 event-replay buffer fixed here). Not acted on
autonomously since it trades away queryable history, not just disk space.
