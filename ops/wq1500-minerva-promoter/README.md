# wq#1500 — Minerva promoter: parked proposals become wq tickets

Design: `PROMOTER_JUDGE_REPAIR_DESIGN.md` §2a (worker-readable copy at
`/home/lumen/research/minerva-ref/`, the sweep-gated hourly mirror of the live minerva tree).

## What is already live (verified 2026-09-13, not restated from the ticket)

The §2a code landed in the **minerva repo**, not here: `minerva_promote.py` (494 lines, symlinked
as `/home/lumen/bin/minerva-promote` since 2026-09-08) plus the `_promote_parked` hook that
`minerva_factory.cmd_run` and `cmd_replan` call after planning every YELLOW/RED row
(`minerva_factory.py:986,1022,1027-1044` in the mirror). wq#1535 (foreman PR #364, minerva PR #9)
then rewrote the ticket body so it only cites worker-readable roots. As of today 23 open/blocked/done
tickets in queue.db carry the `minerva_proposal=<id>` marker (wq#1502-#1520 range), and the factory
timer promotes as it plans.

What the live code does, per §2a:

- additive `proposals.wq_ticket INTEGER` column (`migrate()`), proposal status never changed;
- eligible statuses `deploy_ready|planned|staged`; green-applied and `rejected*` plans skipped;
- idempotent three ways: column set -> `already`; `minerva_proposal=<id>` found in any wq task
  detail -> `relinked`; wq's own normalized-title duplicate refusal -> `duplicate-title`;
- priority: `CVE-\d{4}-\d+|GHSA-…|advisory|security (patch|fix|update|vulnerab)` anywhere in title,
  proposed_change, current_state or the source-intel title/source -> **pri 1** with `[SEC] ` title
  prefix; else factory YELLOW -> 2, RED/unplanned -> 3. Lane `fleet` (`MINERVA_PROMOTE_LANE`);
- detail = grounding block (marker, intel title/url, current_state, proposed_change, verify scores +
  judge rationale, factory plan lane/reasons, published `proposal.md`/`plan.md`, how to close);
- same-product upgrade proposals collapse into one ticket (`fleet_stack.json` aliases);
- kill switch `MINERVA_PROMOTE=0`; backfill CLI `minerva-promote run [N]` (rows created on/after
  `MINERVA_PROMOTE_SINCE`, default 2026-09-03 = wq#1383 grounding fix); `status`, `one`, `preview`,
  `reemit`.

## What this directory adds (the foreman-side gap)

1. **`minerva-promote.service` / `.timer`** — the backfill had no schedule. The factory hook only
   sees rows it plans *this* run; rows parked before the hook existed, rows the hook fail-opened on
   (wq add error, wq db locked), and anything `minerva-factory replan` re-parks were only promoted
   when someone ran `minerva-promote run` by hand. The timer runs `run 200` at
   `03,07,11,17,19,23:55 UTC`, 20 min after each `minerva-factory.timer` slot. Idempotent, so a
   no-op run costs a handful of SELECTs.
2. **`install.sh`** — installs + enables the timer; refuses when `/home/lumen/minerva/minerva_promote.py`
   is missing or is not the wq#1500 promoter; disables the wq#1396 `minerva-proposal-promoter.timer`
   if it is still enabled (see below).
3. **`test_minerva_promote.py`** — the acceptance replay. minerva's own `tests/` dir is excluded from
   the mirror, so this is the only promoter test a foreman worker can run. It imports the module from
   `MINERVA_REF` (default: the mirror; set it to `/home/lumen/minerva` to test the live copy), pins
   every path constant to a scratch dir, and drives the real `/home/lumen/bin/wq` against a scratch
   `WQ_DB`. 24 checks: eligibility, since-cutoff, green-applied skip, CVE -> [SEC] P1, GHSA in intel
   title -> P1, YELLOW -> P2 with `F<plan>.approved` step, unplanned -> P3, lane fleet, marker +
   grounding block + verify scores in detail, no `/home/lumen/minerva` citation, published md,
   idempotent second run, marker recovery after the column is wiped, `MINERVA_PROMOTE=0`,
   Langfuse two-proposal collapse. **24/24 pass 2026-09-13** against mirror sync of the same day.

```
python3 ops/wq1500-minerva-promoter/test_minerva_promote.py            # against the mirror
MINERVA_REF=/home/lumen/minerva python3 ops/wq1500-minerva-promoter/test_minerva_promote.py  # live copy
sudo ops/wq1500-minerva-promoter/install.sh
```

## wq#1396 predecessor is superseded

`ops/wq1396-minerva-proposal-promoter/` (PR #277) was an earlier, narrower promoter: column
`proposals.ticket`, marker `minerva proposal <id>`, lane `build`, no `[SEC]` prefix, no grounding,
no planned-status coverage. Its unit files are **not** installed live (`systemctl cat` finds none;
`/home/lumen/bin/minerva-proposal-promoter` is absent), so it is inert today, but its `install.sh`
would have re-enabled it. Running both would double-file every CVE proposal: the two promoters share
neither column nor marker, and the `[SEC] ` prefix defeats wq's title dedup. That install script now
refuses and points here; its two live tickets (wq#1397 done, wq#1398 blocked) are unaffected.

## Operate

```
/home/lumen/bin/minerva-promote status          # parked vs promoted per status
/home/lumen/bin/minerva-promote run 200         # backfill now
journalctl -u minerva-promote -n 30             # last timer runs
MINERVA_PROMOTE=0                               # kill switch (factory hook + timer)
```

Rollback: `sudo systemctl disable --now minerva-promote.timer`. Tickets already filed stay; the
column stays (additive). Nothing in the minerva repo is touched by this directory.
