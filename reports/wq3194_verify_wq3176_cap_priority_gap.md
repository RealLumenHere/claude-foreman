# wq#3194 verify: wq#3176 [aq] 622 open alert rows dominated by Failed/Recovered pairs

Verdict: **CONTRADICT** (escalates per ticket policy to opus-5@high judgment)

PR: foreman PR#1460, merged to `main` as `bbf15947` (identical content to the
author's branch tip `953ab5e1`, `feat/wq3176-aq-622-open-alert-rows-dominated-by-fail-a1`).
Diff verified: `git show bbf15947` — three new files, no edits to existing code:
`ops/wq3176-aq-daily-ack-extend/{README.md,aq_daily_ack_extend.py,tests/test_aq_daily_ack_extend.py}`.

## What wq#3176/PR#1460 claimed

622 open `aq` rows, dominated by `failed-unit` Failed/Recovered twins that never
pair off and by recurring info-class sources the existing daily auto-ack
(`aq_autoack.py`, outside this repo) can't reach because it only acks `count=1`
rows. `aq_daily_ack_extend.py` adds three rules against the same `alerts.db`:

1. Pair-ack: a `Recovered: <unit>` row newer than its `Failed unit: <unit>`
   twin means the unit is back up — ack both.
2. Stale info-class ack: same info tier (`min/low/default/1/2/3`) and 7-day
   window as `aq_autoack.py`, minus the `count=1` restriction.
3. Per-source display cap: any source with more than `PER_SOURCE_CAP` (default
   15) open rows keeps only its N most-recently-active rows; older overflow is
   auto-acked.

The module docstring (`aq_daily_ack_extend.py:28-31`) states, for all three
rules together: "All three ack (never resolve/delete) with an audit note...
**Never touches high/urgent priority (4,5,high) -- those are the real rows
this ticket exists to surface.**" kb/ISSUE-LOG.md#1086 records the author ran
this against a `.backup`-pinned copy of the live `alerts.db`: 637→169 open
rows.

## What I checked

Read `aq_daily_ack_extend.py` (156 lines) and its test file (141 lines) cold,
then ran the shipped suite:

```
cd /home/lumen/worktrees/wq3194-a2 && python3 -m pytest \
  ops/wq3176-aq-daily-ack-extend/tests/test_aq_daily_ack_extend.py -v
```
9 passed. Rules 1 and 2 (`ack_recovered_failed_pairs`, `ack_stale_info_class`)
match their stated behavior:
- Rule 1's `<=` comparison correctly leaves a pair alone when the failure is
  the more recent event (`test_failed_newer_than_recovered_leaves_both_open`),
  and leaves an orphaned `Recovered:` row alone when no open twin exists.
- Rule 2's query (`aq_daily_ack_extend.py:118-122`) filters on
  `priority IN (INFO_TIER)` and excludes `[deploy_ready]`/`[blocked]`/`[defer]`
  titles — genuinely never reaches priority 4/5/high rows.
- The single-open-row-per-key assumption Rule 1 relies on is consistent with
  `ops/r4_undetected_sensors.py`'s own handling of the same `failed-unit`
  source convention (count aggregates onto one open row rather than duplicate
  rows).

## Contradiction found (CONFIRMED)

**`cap_per_source` (Rule 3, `aq_daily_ack_extend.py:126-140`) has no priority
filter at all** — `priority` does not appear anywhere in its query or ack
path:

```python
sources = [r["source"] for r in c.execute(
    "SELECT source FROM alerts WHERE status='open' GROUP BY source "
    "HAVING COUNT(*)>?", (cap,)).fetchall()]
...
rows = c.execute(
    "SELECT id FROM alerts WHERE source=? AND status='open' "
    "ORDER BY last_ts DESC", (source,)).fetchall()
overflow = [r["id"] for r in rows[cap:]]
n += _ack_ids(c, overflow, f"per-source display cap ({cap}) on {source}")
```

This directly contradicts the module's own docstring claim that "all three"
rules never touch high/urgent priority. It is not a hypothetical edge case —
the shipped test itself proves it: `test_cap_per_source_keeps_newest_and_acks_overflow`
(`tests/test_aq_daily_ack_extend.py:125-133`) inserts 5 rows on source
`jay-watch` with `priority="high"` and asserts 2 of them get acked by
`cap_per_source`. `jay-watch` is the exact source the README names as Rule 3's
motivating example (`README.md:24`). The author's own fixture demonstrates the
contradiction; it just isn't read as one because nothing in the suite asserts
the priority invariant.

I additionally wrote an independent probe (not reusing the shipped fixture)
against the unmodified module, `/tmp/fworker-wq3194/probe_cap_priority.py`:
5 open rows, single source, all `priority='high'`, cap=3.

```
$ python3 /tmp/fworker-wq3194/probe_cap_priority.py
cap_per_source acked 2 row(s); of those, 2 were priority=high: ['incident-0', 'incident-1']
CONTRADICTION: docstring claims 'never touches high/urgent priority
(4,5,high)' for all three rules, but cap_per_source acked high-priority rows
above.
```
(exit 1 on contradiction, exit 0 otherwise — falsifiable both ways, per
wq#2630.)

**Production impact**: once any source — regardless of severity — accumulates
more than `PER_SOURCE_CAP` (15) concurrently open rows, the oldest overflow is
silently auto-acked daily, including active high/urgent incidents. This is the
exact failure mode the ticket exists to prevent ("those are the real rows this
ticket exists to surface"): a noisy urgent source would have its own real,
unresolved alerts buried by this rule, not surfaced by it. The 637→169 backup
run cited in kb/ISSUE-LOG.md#1086 does not rule this out — nothing in that
note breaks down how many of the ~468 acked rows came from Rule 3 versus
Rules 1/2, or what priority they carried.

## Secondary gap: no `--verify` handler

Per `ops/VERIFY_CONTRACT.md`, this bundle (`ops/wq3176-aq-daily-ack-extend`)
ships no `install.sh`, so it has no `--verify` handler — flagged in this
ticket's own text ("Bundle(s) without a --verify handler:
wq3176-aq-daily-ack-extend. Add one per ops/VERIFY_CONTRACT.md before
confirming"). Not adding one here: the verdict is CONTRADICT, not CONFIRM, and
per ticket instruction ("Do NOT re-implement; verify") writing installation/
verification scaffolding around code with a live correctness bug is premature
— it belongs with the fix. Flagging for whoever picks up the fix-and-wire
follow-up (same person who wires this into `aq_autoack.py`'s `run()` per the
README's own follow-up note).

## Things I looked for and did not find a contradiction in

- Rule 1/Rule 2 priority and staleness logic — correct as documented (above).
- SQL injection / parameter binding in `_ack_ids` — all placeholders bound,
  no string-interpolated values.
- Idempotency — re-running `run()` against an already-acked row is a no-op
  (queries filter on `status='open'`).
- Could not check against the live `alerts.db` (`/home/lumen/agents/alert-queue/`
  is outside this worktree's `--add-dir` grants, same constraint the README
  itself documents) — not needed here since the contradiction is fully
  demonstrated by the shipped code and a deterministic, re-runnable fixture.

## Action taken

Contradiction posted to wq#3176 via `wq note`. Not re-implementing/fixing
`aq_daily_ack_extend.py` per ticket instruction. Fix for whoever picks up the
follow-up: add a `priority NOT IN ('4','5','high')` (or equivalent) filter to
`cap_per_source`'s source-selection and overflow queries
(`aq_daily_ack_extend.py:130-137`), update
`test_cap_per_source_keeps_newest_and_acks_overflow` to prove the invariant
instead of disproving it, and add the `ops/VERIFY_CONTRACT.md` `--verify`
handler noted above.
