# wq#1396 — minerva proposal -> wq ticket promoter

## Root gap

The minerva-classifier/proposal/eval timer chain runs every ~4h and, since
wq#1383, produces GROUNDED proposals — but nothing ever turned an
accepted proposal into work anyone would do. Every row in
`minerva.db::proposals` sat at status `deploy_ready`/`staged` forever with no
`ticket` recorded, so the fleet rediscovered its own security debt (e.g. the
`CVE-2026-66877` Gitea proposal) every cycle and did nothing — Frank had to
hand-carry it into a ticket himself.

## Components

- `minerva_proposal_promoter.py` — reads `minerva.db::proposals` for rows in
  status `deploy_ready` or `staged` with no `ticket` filed yet, files a
  `wq add` ticket per row (`--lane build`, `--archetype standard-engineering`,
  detail = proposed_change + rollback + risk + source_intel), and writes the
  new ticket id back to `proposals.ticket`. Security/CVE-flavored proposals
  (title/proposed_change/current_state matching `CVE-\d{4}-\d+`,
  "vulnerab*", "exploit", "RCE", "unauthenticated", etc.) go out at `--pri 1`;
  everything else at `--pri 3`. Installed to `/home/lumen/bin`.
  - Self-migrates: adds the `proposals.ticket` column on first run if it
    isn't there yet (`ensure_ticket_column`).
  - Idempotent two ways: (1) `proposals.ticket` already set -> skipped
    outright; (2) `proposals.ticket` unset but a wq ticket already carries
    this promoter's `(auto-filed by minerva-proposal-promoter from minerva
    proposal <id> ...)` marker in `detail` (crash between `wq add` succeeding
    and the DB writeback) -> that ticket is found and backfilled instead of
    re-filed. `wq add`'s own title-dedup is a second safety net on top.
- `minerva-proposal-promoter.service` / `.timer` — `OnCalendar` 15 minutes
  after each `minerva-eval.timer` slot (`03,07,11,17,19,23:15:00 UTC`), so a
  proposal minerva-eval marks `deploy_ready` this cycle files as a ticket
  before the next one.
- `install.sh` — idempotent deploy (script + units + `enable --now`).

## Usage

```
minerva-proposal-promoter [--dry-run] [--limit N]
    [--db /home/lumen/minerva/minerva.db]
    [--wq-bin /home/lumen/bin/wq]
    [--wq-db /home/lumen/agents/work-queue/queue.db]
```

`--dry-run` classifies and logs what would be filed without calling `wq add`
or touching either database.

## Test procedure (no live DB or ticket touched)

```
python3 ops/wq1396-minerva-proposal-promoter/test_minerva_proposal_promoter.py
```

Runs the real `/home/lumen/bin/wq` (env-overridable via `WQ_BIN`) against a
scratch `WQ_DB` and a scratch minerva-shaped sqlite DB. Covers: CVE proposal
promotes at P1/build, non-security proposal at P3/build, a `pending`-status
proposal is left alone, a second run against the same DBs files no
duplicate tickets, and a simulated crash (ticket filed in queue.db, `ticket`
column never written back) is recovered by backfill rather than re-filed.
13/13 checks pass as of 2026-09-03.
