# R4 ledger — partial-week trend fix (design before build)
## session wq925-r4-8030d2, 2026-08-27

## ROOT CAUSE (evidence grade A, reproduced live)
`emit()` sets headline/trend/delta from the LAST key in the weeks map, which is
always the current week-to-date. On 2026-08-27 (Thu, day 4/7 of W35) it emitted
`headline=0, delta=-13, trend="down"` (green down-arrow) — comparing 4 days of W35
against a full 7-day W34. The arrow is structurally over-optimistic Mon-Wed and
"regresses" as the week fills. The one number Frank reads is a lie early in the week.

## CLASS
Same class as DELIVERY_FAILURE Case 5 (the queue that lies costs trust every open).
A misleading trend is worse than no trend: it tells Frank the fleet needs him less
precisely when the week has not happened yet.

## FIX (minimal, no schema change)
1. HEADLINE + TREND + COLOR are driven by the last COMPLETE ISO week vs the week
   before it. That answers the acceptance question ("more or less than last week?")
   using two full weeks. W34(13) vs W33(11) -> delta +2 -> trend "up"/RED. Honest.
2. CURRENT partial week rides along as `in_progress`: its to-date events, plus a
   `pace_vs_prev` = (events so far this week) - (prior week's events through the SAME
   weekday). This is the only fair partial comparison. It never drives the headline
   color; it is labelled "to date (pace)".
3. `weeks[]` trend rows unchanged (full history, incl. current partial, clearly last).

## same-weekday partial
New helper `events_through_weekday(c, week, dow)`: count events in `week` with
date <= that week's Monday + (dow-1) days. Used to compute prior-week pace baseline.

## WIDGET
glance.yml template updated: big number = last complete week + arrow; a second line
"W35 to date: N (pace +/-M vs same point last wk)". parked/undetected unchanged.

## ROLLBACK
intervention_ledger.py.bak.20260827_140340_pre_partialweek_fix ;
glance.yml.bak.*_pre_r4_partialweek. Revert file, re-run `run`, restart glance.

## TEST TO FAILURE
- run with today (Thu) -> headline=13 (W34), trend up/red, in_progress W35=0 pace vs
  W34-through-Thu.
- simulate: inject a synthetic event into a fake "current week" and confirm headline
  still tracks last COMPLETE week, not the partial.
- edge: only one week of data -> no prev -> trend flat, in_progress present.
- edge: Monday (dow=1) -> prior-week baseline = prior Monday only.
