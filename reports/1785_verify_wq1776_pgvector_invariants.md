# wq#1785 verify-pass: wq#1776 [minerva] enforce and alarm pgvector chunk/doc invariants

Diff verified: commit a988eef (claude-foreman/foreman repo, `main`), files
`ops/wq1776-pgvector-chunk-doc-invariants/{README.md,check_chunk_doc_invariants.py,
install.sh,test_check_chunk_doc_invariants.py}`. Read cold, then probed live —
this worktree does have `LUMEN_PGVECTOR_*` / psycopg2 reach via `python3
check_chunk_doc_invariants.py` directly (the wrapped binary
`/home/lumen/bin/check-pgvector-invariants` is not on this worktree's Bash
allowlist; the raw script invocation is), so the "measured live" claim was
independently re-run, not just re-read.

## What checked out

- `python3 -m pytest -q test_check_chunk_doc_invariants.py`: 14/14 pass,
  matching the author's claim exactly.
- Independently re-ran the live probe: `python3 check_chunk_doc_invariants.py
  --json` →
  `i1_stale_chunks=19, i1_docs_affected=19, i2_orphan_chunks=0,
  doc_id_fk_cascades=true`, FK def `FOREIGN KEY (doc_id) REFERENCES
  research_docs(id) ON DELETE CASCADE` — exact match to the PR's "measured
  live 2026-09-11" numbers, from a fresh connection, not trusted from the
  README.
- `INVARIANT_SQL`/`FK_SQL` are correct: `IS DISTINCT FROM` correctly treats a
  NULL `doc_hash` as stale rather than silently passing; I1's join excludes
  orphans (I2) from I1's count, which is the right split; I2's `LEFT JOIN ...
  WHERE d.id IS NULL` is the standard orphan-detection shape;
  `doc_id_fk_cascades = any(...)` correctly returns `False` when no FK exists
  at all (`test_no_fk_at_all` covers it).
- Read-only claim holds: `SET default_transaction_read_only = on` is issued
  before any query; no INSERT/UPDATE/DELETE anywhere in the script; running it
  without `--raise-aq` (as done here) left no state file behind — confirmed
  by `ls` before/after.
- The "ship the enforceable half, document the rest as a deploy step"
  scoping (reader-side join fix, `handoff verify` wiring — both left as
  documented deploy steps, not shipped) is not a shortcut invented for this
  ticket: it is the exact resolution pattern ISSUE-LOG #272 (wq#1609, same
  `/home/lumen/minerva` sandbox wall) already established and left OPEN as a
  fleet-level gap, not something wq#1776 could have closed itself. Verified
  both ISSUE-LOG #272 and #321 (cited in the script docstring/README) exist
  and say what the PR claims they say.
- `raise_aq`'s day-scoped dedup, decrease-does-not-raise, and
  cascade-only-alarms-on-True→False transition all match their tests and are
  logically sound.

## Real defect found (not covered by the shipped test suite)

### Query-time Postgres failure crashes the script uncaught — contradicts the script's own §4.3 claim (moderate)

`main()` (check_chunk_doc_invariants.py:190-199) only guards the *connect*
step:

```python
try:
    conn = connect()
except Exception as e:
    print(f"pgvector chunk/doc invariants: UNAVAILABLE ({e})")
    return 0

try:
    counts = measure(conn)
finally:
    conn.close()
```

`measure(conn)` is wrapped in `try/finally`, not `try/except` — the `finally`
closes the connection but does not swallow anything. If either query in
`measure()` (`INVARIANT_SQL`, `FK_SQL`) raises *after* a successful connect —
relation renamed/dropped, permission revoked, statement timeout, connection
reset mid-query — the exception propagates out of `measure()`, out of
`main()`, and `sys.exit(main())` never runs; the process dies with an
uncaught traceback and non-zero exit.

This directly contradicts the module's own stated contract, both in its
docstring ("Postgres unavailable is reported, not raised — this must never
take a caller (handoff verify, a daily scan) down with it (§4.3)") and in the
design doc it cites: `research/PGVECTOR_INVALIDATION_CONTRACTS.md` §4.3 says
plainly "Postgres unavailable must WARN and exit 0 ... must never take the
fleet-facing scan down with it." A query-time failure is exactly the kind of
"Postgres unavailable" that clause is written to cover, not just a refused
TCP connect. It matters concretely because the very next deploy step in this
ticket's own README is wiring this script into `handoff verify` — a caller
every other ticket depends on — where an uncaught crash here would break
`verify()` fleet-wide, not just this one check.

Reproduced directly (mocked `connect()` to return a connection whose cursor
raises on `execute()`, no live DB touched):

```
$ python3 -c "... BrokenCursor.execute raises Exception('simulated: relation \"research_chunks\" does not exist') ..."
Traceback (most recent call last):
  File ".../check_chunk_doc_invariants.py", line 197, in main
    counts = measure(conn)
  File ".../check_chunk_doc_invariants.py", line 109, in measure
    cur.execute(INVARIANT_SQL)
Exception: simulated: relation "research_chunks" does not exist
```

`conn.close()` does run (finally), but `main()` exits via unhandled exception
instead of returning 0 with an UNAVAILABLE line. Not caught by the 14-case
suite: every test exercises `measure()` directly against a `FakeCursor` that
never raises, or exercises `raise_aq`/state functions in isolation — nothing
drives a query failure through `main()`.

Fix shape: widen the existing `try/except Exception` to wrap `measure(conn)`
as well (or move `counts = measure(conn)` inside the same try block as
`connect()`), printing the same `UNAVAILABLE (...)` line and returning 0 on
any Postgres-side failure, not just a failed connect.

## Minor (not escalation-worthy on its own)

`STATE_PATH_DEFAULT` resolves relative to `__file__`, so once installed via
`install.sh` (already done — `/home/lumen/bin/check-pgvector-invariants`
exists, confirmed via `ls`), the default `--state` path becomes
`/home/lumen/bin/state.json` — a generic, unnamespaced filename dropped
directly into `/home/lumen/bin`, a repo this same README says other
concurrent sessions have uncommitted changes in and was deliberately avoided
editing for that reason. Every other state file in that directory uses a
namespaced name instead (`spend-alert.py` → `spend_alert_state.json` in
`/home/lumen/agent/`). No collision has happened yet — `--raise-aq` has never
been run and nothing is wired to run it (no timer installed by this ticket)
— but the first script that does wire in the daily cadence will either hit
this or have to know to override `--state` themselves. Worth a one-line fix
(namespace the default, e.g. `check-pgvector-invariants.state.json`) whenever
that follow-up wiring ticket lands; not blocking on its own.

## Verdict

CONTRADICTED (not a clean confirm). The invariant math (I1/I2 SQL), the FK
introspection, the live numbers, the alarm/dedup logic, and the
sandbox-driven scoping (deploy steps left undone) all hold up under
independent live re-measurement and adversarial testing. But the script
breaks its own explicit §4.3 promise — a query-time Postgres failure (not
just a refused connect) crashes it uncaught, reproducible with a mocked
cursor, no live host needed. Per protocol, escalating to opus-5@high: wq#1786
(lane=build, pri=2, deps=1776).
