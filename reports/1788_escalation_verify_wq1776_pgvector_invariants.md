# wq#1788 — verify-pass escalation: opus-5@high re-check of wq#1776

**Target** wq#1776 / claude-foreman PR#490, commit `a988eef`,
`ops/wq1776-pgvector-chunk-doc-invariants/`
**Escalation of** wq#1785 (sonnet-5@high, fresh instance) → CONTRADICT
**Trigger** `research/models.yaml` `verify_pass.escalate_on_contradiction: opus-5@high`
**Date** 2026-09-20 · reviewer: opus-5@high, fresh instance, no prior context

## Verdict: **CONFIRM** — wq#1785's CONTRADICT stands

The finding was re-derived from the source, not read off wq#1785's transcript, and
independently reproduced. The repro is committed alongside this report so the next
reviewer does not have to take either verify pass on faith.

## The defect

`check_chunk_doc_invariants.py:190-199`:

- `connect()` is wrapped in `try/except Exception` → prints the `UNAVAILABLE` line,
  `return 0`. Correct.
- `measure(conn)` is wrapped in a bare `try/finally` with **no** `except`. Every
  Postgres-side failure that lands after a successful `connect()` — connection reset,
  server restart, statement timeout, dropped/renamed relation, revoked grant —
  propagates out of `main()` uncaught: traceback on stderr, non-zero exit.

Both queries `measure()` runs are exposed. `FK_SQL` is the more brittle of the two:
`'research_chunks'::regclass` raises `UndefinedTable` outright if the relation is ever
renamed.

### What that contradicts

- The script's own docstring, `check_chunk_doc_invariants.py:39-40`: *"Postgres
  unavailable is reported, not raised — this must never take a caller (handoff verify,
  a daily scan) down with it (§4.3)."*
- `research/PGVECTOR_INVALIDATION_CONTRACTS.md:333-335` §4.3, verbatim: *"Postgres
  unavailable must `WARN` and exit 0 on the corpus half while the sqlite half still
  runs; the corpus is the less critical of the two and must never take the fleet-facing
  scan down with it."*

## Repro (no live DB, no real `.env`)

`ops/wq1788-verify-wq1776-pgvector-invariants/repro_query_time_failure.py` stubs
`psycopg2` and drives `main([])` through both §4.3 windows. Case A is the control:
it proves the intended behaviour exists and that the harness is faithful.

```
--- case A: unreachable at connect() (control)
    rc           : 0
    stdout       : pgvector chunk/doc invariants: UNAVAILABLE (could not connect to server: Connection refused)
    raised       : (nothing)
    HONOURS §4.3 : True
--- case B: connect() succeeds, query fails
    rc           : None
    stdout       : (nothing)
    raised       : OperationalError: server closed the connection unexpectedly
    HONOURS §4.3 : False

VERDICT: control passes=True  query-window passes=False
```

The stub points `LUMEN_AGENT_ENV` at a nonexistent path before import, so `_load_env()`
is a no-op and `/home/lumen/agent/.env` is never opened.

## The one counter-argument, and why it fails

The strongest defence of `a988eef` is that §4.3 says *"Postgres unavailable"*, and a
dropped relation or a revoked grant is not unavailability — it is a real schema defect
that arguably *should* be loud.

That defence does not save the code, because **case B above is a plain connection loss**
between `connect()` and the first `execute()`. That is unambiguously "Postgres
unavailable" under any reading of §4.3; it is simply the race window the current
`except` does not cover. A Postgres restart during the scan is not an exotic input. So
the defect stands even under the narrowest possible reading of the contract, and the
schema-defect cases are additional exposure on top, not the load-bearing argument.

## New evidence this pass adds (not in wq#1785)

**`main()` has zero test coverage.** The 14 shipped tests exercise `measure()`,
`line()`, `raise_aq()`, `_aq_add()` and the state round-trip, but
`test_check_chunk_doc_invariants.py` never calls `m.main(...)` — the only `main(` in the
file is `unittest.main()` at line 164. The §4.3 guarantee lives entirely inside
`main()`, so nothing in the suite would have caught this, and nothing will catch a
regression after the fix either. The fix should land with a test that drives `main()`
against a stubbed query failure.

**Blast radius today is zero; the ticket's severity is correctly future-conditional.**
`grep -rn 'check-pgvector-invariants' /home/lumen/bin/` returns no callers other than
the installed script itself, and `README.md:124-125` says the `handoff verify` wiring is
*"not wired in by this ticket."* The defect is latent. It becomes fleet-facing at the
exact deploy step the README documents as next, which is why it should be fixed before
that wiring lands rather than after.

## Fix shape (confirmed as stated in wq#1785)

Widen `main()`'s existing `try/except Exception` to cover `measure(conn)` as well —
either move the call into the same `try` as `connect()`, or give the second block its
own `except Exception` printing the same `UNAVAILABLE` line and returning 0. Keep the
`finally: conn.close()`. Add a `main()`-level test for the query-failure path.

## Secondary finding: CONFIRMED, still non-blocking

`STATE_PATH_DEFAULT` (`check_chunk_doc_invariants.py:52-55`) derives from
`os.path.dirname(os.path.abspath(__file__))`. `install.sh` installs the script to
`/home/lumen/bin/check-pgvector-invariants`, so the installed copy's default state file
is the un-namespaced `/home/lumen/bin/state.json`. Verified: the installed binary exists
(`/home/lumen/bin/check-pgvector-invariants`, 7740 bytes, matching source);
`/home/lumen/bin/state.json` does **not** exist yet, because `--raise-aq` has never been
run. So this is confirmed-in-waiting — the first `--raise-aq` run drops a generic
`state.json` into `bin/`. Fix whenever `--raise-aq` gets a cadence; a namespaced default
(`/home/lumen/agent/pgvector_invariants_state.json`, matching `spend_alert_state.json`)
is a one-line change.

## Re-checked, holds — not re-litigated further

Per the escalation brief these were independently re-measured by wq#1785 and are
accepted: 14/14 tests pass (re-run here: `14 passed in 0.08s`); live counts
`i1_stale_chunks=19`/19 docs, `i2_orphan_chunks=0`, `doc_id_fk_cascades=true`;
`INVARIANT_SQL`/`FK_SQL` logic (`IS DISTINCT FROM` null-handling, orphan `LEFT JOIN`
shape, `any()` cascade check); read-only claim; and the deferred reader-join /
handoff-verify scoping matching the ISSUE-LOG #272 (wq#1609) sandbox-wall precedent.
Nothing found this pass disturbs any of them.

## Lane check

wq#1785 dispatched three lanes. `feat/...-a1` and `feat/...-a2` never produced a verify
commit — both tips sit at `80f6298`, the shared base. Only `a3` delivered
(`8900126 docs(verify): wq1785 CONTRADICT wq1776 pgvector invariants`). There is no
dissenting sibling verdict to weigh against the CONTRADICT.

## Record discrepancy

The wq#1788 ticket text cites `ISSUE-LOG.md#328` for wq#1785's entry. Entry 328 is a
different issue (wq#1793, UHP conformance spike); the log's last entry is 478. The
citation is stale, not the finding — the wq#1785 report is real (commit `8900126` on
`feat/wq1785-verify-wq-1776-minerva-enforce-and-alarm-a3`).

## ISSUE-LOG entries this pass could not append

`/home/lumen/kb` is a primary tree: `harness/guard.sh` denies Write/Edit there for
workers (wq#1719/wq#900) and tells you to land the change from your own worktree — but
this is a `claude-foreman` worktree and `ISSUE-LOG.md` lives in the separate `kb` repo,
which has no worktree here. So a foreman BUILD worker structurally cannot append to the
ISSUE-LOG, even though the standing doctrine tells it to. The three entries this pass
owes the log are recorded here instead, for whoever holds a `kb` checkout:

| n | symptom | root cause | solution | status |
|---|---|---|---|---|
| 479 | wq#1788 ticket cites `ISSUE-LOG.md#328` as wq#1785's entry, but 328 is wq#1793 (UHP spike) and the log's last entry is 478 | verify workers write the ISSUE-LOG append and the done-notes at different moments; the number quoted was taken before the append, and the log had grown ~150 rows since | treat the citation as stale, not the finding; workers quoting a log number in done-notes should append first and quote the number the append actually got | SOLVED |
| 480 | Two Bash shapes refused, neither retryable: (a) `cd <dir> && grep -n pat relative/path` → *"grep reads a file by a relative path after a cd in a compound command; which file that is cannot be resolved statically while a Read() deny rule is configured"*; (b) `cd <dir> && git branch -a` → *"changes directory before running a version-control command, which can pick up untrusted hooks or repository configuration"* | the guard resolves file and VCS targets statically; a preceding `cd` on the same compound line makes both unresolvable | drop the `cd`: give grep an absolute path, and use `git -C <dir> <subcommand>`. Both worked first try in that form | SOLVED |
| 481 | A foreman BUILD worker told by doctrine to append to `/home/lumen/kb/ISSUE-LOG.md` is blocked by `guard.sh` ("primary repo roots are read-only for workers ... land it with lumen-pr"), and has no `kb` worktree to land from | the primary-tree wall (wq#1719/wq#900) is repo-scoped, but the ISSUE-LOG doctrine is fleet-wide; the two have no shared landing path | recorded the owed entries in this report instead. Real fix: either give workers a `kb` worktree alongside the foreman one, or move the ISSUE-LOG append behind a `bin/` helper that writes on the worker's behalf | OPEN |
