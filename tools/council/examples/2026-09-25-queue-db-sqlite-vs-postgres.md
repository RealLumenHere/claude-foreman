# Research council

- **claude** (`claude-max-opus`): no answer, TimeoutError: timed out
- **gemini** (`gemini-flash`): Response A, 35.6s
- **deepseek** (`deepseek-v4-pro`): Response B, 202.7s
- **gpt** (`cerebras-oss-120b`): Response C, 2.7s
- chairman: `deepseek-v4-pro`, total 326.2s

## Consensus

- SQLite in WAL mode still permits only one writer at a time; with multiple writers this causes `SQLITE_BUSY` / `database is locked` errors (A, B, C).
- `busy_timeout` helps wait out transient locks, and `BEGIN IMMEDIATE` plus short write transactions avoids the common deferred-transaction lock-upgrade deadlock (A, B, C).
- Those SQLite mitigations reduce errors but do not remove the single-writer serialization point; under enough sustained write load there is still a throughput ceiling (A, B, C).
- Lease-expiry and task-claiming races must be handled with atomic compare-and-swap / transactional update patterns, not naive `SELECT` then `UPDATE` (A, B, C).
- A single-writer daemon would serialize writes and can make SQLite safe, but introduces a central process, extra IPC, possible latency, and a single point of failure/bottleneck (A, B, C).
- Postgres would remove SQLite’s database-level write contention through MVCC and row-level locks, and supports row-locking patterns such as `SELECT ... FOR UPDATE` / `SKIP LOCKED`; it also adds server management, connection handling, and migration work (A, B, C).
- Dedicated queues/brokers such as Redis, RabbitMQ, or Kafka can handle high concurrency and native acknowledgement/lease semantics, but add operational complexity and may not preserve relational querying (A, B, C).
- SQLite is only suitable for a single-host, local-disk deployment; multiple hosts or unreliable network filesystems would force a move away (A, B, C).

## Disagreements

### Main recommendation

- **A** recommends moving to Postgres now.
- **C** recommends moving to Postgres unless you are strictly single-host, zero-admin, and willing to add a single-writer daemon.
- **B** recommends staying on SQLite first and applying `busy_timeout`, `BEGIN IMMEDIATE`, and atomic lease SQL, with Postgres as a later step only if measurements justify it.

Evidence favours **B’s immediate recommendation for the stated workload**. The observed “occasional database is locked under burst load” is exactly the classic WAL-mode deferred-transaction problem that `BEGIN IMMEDIATE` is designed to prevent, and lease-expiry races are fixable with atomic `UPDATE ... WHERE` compare-and-swap statements. A and C are correct that Postgres is the safer scaling path, but at ~15 writers on one host with small queue-state writes, moving databases first is not clearly necessary.

### Whether 15 writers already exceeds SQLite’s practical capacity

- **A/C** treat 15 concurrent writers as high SQLite write contention; C goes further with specific unsourced throughput/latency figures.
- **B** argues this is likely a transaction-pattern problem, not a capacity problem, and that local SQLite can handle hundreds to low-thousands of small write transactions per second.

Evidence favours B. SQLite serializes writes, but short local transactions can still be very fast. The current failure is more consistent with lock-upgrade races and missing atomic lease logic than with a proven SQLite capacity limit.

### Whether a single-writer daemon should be part of the SQLite fix now

- **A/C** present a single-writer daemon as a key or safest SQLite option.
- **B** says do not add a daemon yet; fix the transaction pattern first.

Evidence favours B. The daemon would remove most contention by construction, but it adds a new service, IPC, monitoring, and a single point of failure. Direct worker transactions with `BEGIN IMMEDIATE` should be tried first.

## Unique points

- **B** provides concrete SQLite PRAGMAs, atomic claim/renew/complete SQL, and the important requirement to open connections after `fork`. These hold up and are the most useful implementation details.
- **B** notes that moving to Postgres does not automatically fix lease races: naive `SELECT` then `UPDATE` in Postgres can still double-claim jobs. This is correct.
- **B** notes that queue-like Postgres tables create dead tuples and may need autovacuum attention. This is correct.
- **A** claims lease-expiry races show SQLite struggles to provide robust multi-writer semantics without significant application-level serialization. This is overstated: atomic `UPDATE ... WHERE` claims inside `BEGIN IMMEDIATE` are a standard and sufficient SQLite pattern.
- **C** supplies specific benchmark-style numbers about SQLite and Postgres throughput and lock-error rates. These are unsourced and should not be treated as established evidence.
- **C** claims there is a “file-system corruption risk if the host crashes while the WAL file is open.” As stated, this is misleading: SQLite WAL is designed to be crash-safe on local disks when `synchronous` is configured appropriately. The real risk is network filesystems or disabling fsync.
- **C** mentions “`SELECT ... FOR UPDATE SKIP LOCKED` via the sqlite3 `SELECT ... LIMIT 1` trick.” SQLite does not have `SKIP LOCKED`; the valid pattern is an atomic transactional update/claim.

## Synthesis

The best answer is to **stay on SQLite and fix it first**, not to move to Postgres immediately.

Put `queue.db` on a local filesystem, not NFS/SMB. Open SQLite connections after `fork`, and never share one connection across processes.

Apply these settings in every process:

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=FULL;   -- use NORMAL only if losing recent commits on power failure is acceptable
```

For every write, claim, renew, or completion transaction, use:

```sql
BEGIN IMMEDIATE;
...
COMMIT;
```

Keep write transactions tiny: no network calls, sleeps, or external work inside the transaction.

Lease-expiry and claiming must be atomic. For example, requeue expired jobs with:

```sql
BEGIN IMMEDIATE;
UPDATE jobs
SET status='ready',
    lease_owner=NULL,
    lease_expires=NULL
WHERE status='leased'
  AND lease_expires <= :now;
COMMIT;
```

Claim a job with an atomic guarded update:

```sql
BEGIN IMMEDIATE;
UPDATE jobs
SET status='leased',
    lease_owner=:owner,
    lease_expires=:new_expires,
    attempts=attempts+1
WHERE id = (
  SELECT id
  FROM jobs
  WHERE status='ready'
    AND available_at <= :now
  ORDER BY priority DESC, id ASC
  LIMIT 1
)
RETURNING id, payload;
COMMIT;
```

Renew and complete only with ownership and lease guards, so stale workers cannot affect jobs taken over by another worker.

**What the SQLite fix repairs:** the deferred-upgrade lock race that causes many `SQLITE_BUSY` errors, transient writer collisions, and lease-expiry/claim races if written as atomic compare-and-swap statements.

**What it does not fix:** SQLite still has one writer at a time. If sustained write throughput grows, write transactions lengthen, or workers move to multiple hosts, SQLite becomes the bottleneck.

Do not add a single-writer daemon yet. It would serialize writes and reduce contention, but it introduces a new process, IPC, operational complexity, and a single point of failure. Consider it only if workers cannot be changed to use `BEGIN IMMEDIATE`, or if measurements show a long transaction is holding the write lock.

Move to **Postgres** when any of these are true: you need multiple hosts, measured write throughput after the SQLite fixes is near the ceiling, `BEGIN IMMEDIATE` p99 wait remains too high, or you need row-level concurrency/replication. In Postgres, use `SELECT ... FOR UPDATE SKIP LOCKED` or an equivalent atomic claim, and retain application-level idempotency.

A dedicated queue or broker is not the best immediate move. It is justified only if queuing dominates the system, you need very high throughput or sub-millisecond latency, or you already run such infrastructure.

## Confidence

Medium. The SQLite transaction fixes are well-supported, but without measured current write throughput, burst duration, and post-fix p99 lock/wait numbers we cannot be certain SQLite remains sufficient long-term; benchmarking after applying `BEGIN IMMEDIATE` and atomic lease CAS would raise confidence.
