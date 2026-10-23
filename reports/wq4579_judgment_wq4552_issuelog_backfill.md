# wq#4579 judgment: wq#4571 CONTRADICT of wq#4552 — ISSUE-LOG backfill

Ticket: decide whether to backfill the two missing wq#4552-originated `kb/ISSUE-LOG.md` rows,
and whether wq#4552's status/notes need correcting.

Evidence in: `reports/wq4571_verify_wq4552_issuelog_gap.md` (the CONTRADICT), `kb/ISSUE-LOG.md`
row #1540 (the gap record wq#4571 filed).

## Decision

1. **Backfill one row, not two.** `kb/ISSUE-LOG.md` row **#1544** now carries wq#4552's novel
   finding: a @bytebase/dbhub 1.5.0 sqlite source registered with the env-var scheme
   (`DSN=sqlite:///…?mode=ro` + `READONLY=true` + `MAX_ROWS=500`) is **not** read-only — only a
   TOML `[[tools]] name="execute_sql" … readonly=true` block exercises the engine-level gate
   dbhub 0.22.6 added for CVE-2026-61788. Solution column points at
   `ops/wq4514-mcp-install-batch/fleet-state.toml`, the `install.sh`
   `install_fleet_state`/`verify_fleet_state` wiring, and the `smoke_fleet_state.py` probe.
2. **No row for the second finding.** wq#4552's guard.sh PRIMARY-tree Write/Edit block
   (`fleet_tools.yaml` / `ORCHESTRATOR-HANDOFF.md` under minerva and home-lumen) gets no new
   row: it is already logged five times. Verified, not asserted — each cited row was read and is
   genuinely this class: #309 (wq#1753, `minerva/context/fleet_tools.yaml` — the same file
   wq#4552 was blocked on), #355 (wq#1846, kb Edit refused, quotes the guard string verbatim),
   #521 (wq#1630, `minerva/context/claude_context.sh` + a kb delete), #612 (wq#2316,
   `/home/lumen/bin`), #643 (wq#2434, new file under `/home/lumen/minerva`). A sixth copy is
   noise in a 1500-row table.
3. **wq#4552 stays `done`; PR #2405 stays merged.** The false clause is a self-report about
   bookkeeping, not about the mechanism. The shipped mechanism holds up independently —
   wq#4571's own review (its §"What the diff actually ships") plus the CVE cross-reference
   re-run here. Reopening a ticket whose artifact is correct would churn the lane and lose the
   merged state; the misleading sentence is better fixed where a reader meets it.
4. **Correcting note appended to wq#4552** (`wq note 4552`, 2026-10-23 09:59:31): states the
   `logged kb/ISSUE-LOG.md row…` clause was FALSE, that the mechanism is sound, where the
   backfill landed (#1544), where the gap is recorded (#1540), and that the guard finding was
   deliberately not re-filed. A future worker reading the done-notes now meets the correction in
   the same field as the false claim.

## CVE re-confirmed independently (not inherited)

Row #1544 attributes a `fleet-search web` re-confirmation to wq#4579, so this run actually ran
it rather than inheriting wq#4571's. `fleet-search web "dbhub bytebase CVE-2026-61788 readonly
sqlite execute_sql"` → cve.org `CVERecord?id=CVE-2026-61788`, `nvd.nist.gov/vuln/detail/
CVE-2026-61788`, and CISA's week-of-2026-09-21 summary, all Sep 24 2026, all carrying: "DBHub is
a database MCP server for Postgres, MySQL, SQL Server, Oracle, MariaDB, SQLite. Prior to version
0.22.6, setting `readonly = true` on the `execute_sql` tool does not …". Three independent
sources, matching the row's quoted text.

## Acceptance probe — and the defect it caught

The thing under test is "a worker who searches `ISSUE-LOG` for this gotcha now finds it". Probing
with the row's own vocabulary could only confirm, so the search terms were drawn from **outside**
this ticket: the four greps wq#4571 ran *before* the row existed, extracted from its report
**rev-pinned at `608bb4d3`** (`git show 608bb4d3:reports/wq4571_verify_wq4552_issuelog_gap.md`,
lines 73-77). Those greps returned 0 / 0 / 1-unrelated / 0 at that commit, so the probe had a
demonstrated failing state.

First run — one term still failed:

| pinned term | ISSUE-LOG rows hit |
|---|---|
| `wq4552` | #1540 only — **miss** |
| `4552` | #1540, #1544 |
| `fleet-state` | #1540, #1544, #1546 |
| `fleet_state` | #688, #690, #692, #1527, #1544 |

Row #1544 wrote the id hash-form only (`wq#4552`), so the bare-id grep — the exact form wq#4571
used, and the form a worker searching by ticket reaches for first — skipped the row holding the
answer and found only the row saying the answer was missing. Fixed by appending the bare tokens
to #1544's solution column. Re-run: `wq4552` → #1540, #1544; all four pinned terms now reach both
rows. Practical discovery terms also hit #1544: `dbhub`, `READONLY=true`, `readonly=true`,
`CVE-2026-61788`, `mode=ro`.

`kb/ISSUE-SOLUTIONS-fable.md` stays at 0 hits for `4552`, and that is correct rather than a
second gap: its header dates it `# Issue solutions — Fable pass (2026-09-02)`, a one-off review
of structural issue classes, not a running log that takes new rows. wq#4571 grepped it to prove
the row existed *nowhere*, not to name it a destination.

## Pre-existing dangling citation, now resolved

wq#4552's correcting note and rows #1540/#1544 all cite this report path. The citing text was
written before the file existed (a prior attempt of this ticket was killed mid-run), so between
then and this commit the three citations dangled — the same citation-rot class as row #1541. This
file closes them at the cited path.

## Liveness

Ticket criterion: `grep -c wq4552 kb/ISSUE-LOG.md` → `0` means the gap is open, `>0` means
backfilled. Now `2` (rows #1540, #1544).
