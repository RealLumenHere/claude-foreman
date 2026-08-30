# R4 — INTERVENTION LEDGER (shipped 2026-08-26, session r4-ledger-bbbd4e)

Answers: **is the fleet needing Frank more or less than last week?**
One number, on Glance, trending weekly. Down = good.

## THE NUMBER — baseline, stated honestly

| week | fix (a) | caught-by-Frank (b) | repeats (e) | EVENTS | undetected downtime (d) | parked on Frank (c) |
|---|---|---|---|---|---|---|
| 2026-W31 | 4 | 0 | 0 | 4* | 0* | ? |
| 2026-W32 | 6 | 2 | 0 | 8* | 0* | ? |
| 2026-W33 | 8 | 3 | 0 | 11* | 0* | ? |
| **2026-W34** | **5** | **6** | **2** | **13** | **720 min** | **77 peak → 38** |
| 2026-W35 (to date) | 0 | 0 | 0 | 0 | 0 | 38 |

*Asterisked weeks are regex-mined only (undercounts — nobody was writing incidents
down in a minable form). W34 is the first honest week: regex + the five documented
cases in decisions/DELIVERY_FAILURE_2026-08-22.md + the LPP 12h blind spot. So the
apparent W33→W34 rise is partly measurement, not purely regression. **W34 = 13 is
the baseline of record.** The baseline is bad, as predicted: in one week Frank
personally caught 6 failures the fleet should have caught, had to repeat himself
twice on things the fleet had already written down, and at peak had 77 items
parked on him.

## METRIC DEFINITIONS

- **(a) fix** — Frank had to initiate a fix (directive, reported fault, commissioned
  audit). Includes every `frank_response` on a gated wq ticket.
- **(b) detect** — Frank discovered a failure the fleet should have caught
  (monitoring existed or should have; he was the sensor). Per instincts: when Frank
  is the one who noticed, that is a fleet defect ON TOP of what he noticed.
- **(c) parked** — tickets blocked on a human: `status='blocked'` matching
  Mission Control's own `REVIEW_HUMAN_RE` (frank|gate|approval) over
  block_reason+notes+gate, PLUS user-lane open/blocked. Snapshotted daily.
  Reconciles with MC's review-queue count by construction (same regex).
- **(d) undetected downtime** — minutes a live product/service was down with no
  self-detection before a human noticed. W34 datum: LPP dead 12h (720 min) while
  the audit read a dependency's generate counter as health.
- **(e) repeat** — Frank had to state the same fact/fault again after the fleet
  already had it (P3 plug ×2, Messenger ×2 are the W34 cases).

**Headline = distinct events (a+b+e) per ISO week.** (c) and (d) ride along as
secondary lines. An event can be both a and b; it is recorded under the more
damning category (b > e > a) and counted once.

## COLLECTION — what is automated vs manual

Automated (daily, `lumen-intervention-ledger.timer` 06:15 UTC →
`/home/lumen/foreman/ops/intervention_ledger.py run`):
1. **(c)** computed live from queue.db (read-only), snapshotted daily.
2. **Structured markers** parsed from episode commits and wq notes — the
   high-precision channel going forward. Write these when committing episodes:
   `FRANK-FIX: <desc>` · `FRANK-DETECT: <desc>` · `FRANK-REPEAT: <desc>` ·
   `DOWNTIME-UNDETECTED-MIN=<n>`
3. **wq gated decisions**: any `frank_response` lands as a fix event automatically.
4. **Regex fallback** over episodes/notes (conf=regex, deliberately conservative;
   `regex_share` in the JSON shows how much of a week is low-confidence).

Manual: incidents documented only in prose get `add-event --ts --kind --desc --ref`.
W34's five DELIVERY_FAILURE cases entered this way, refs traceable.

## IMPLEMENTATION

- Collector/CLI: `/home/lumen/foreman/ops/intervention_ledger.py`
  (`run` / `backfill --since` / `add-event` / `show`). Own store:
  `intervention_ledger.db` (same dir). All sources opened `mode=ro`.
- Outputs (atomic write, 0644): `/home/lumen/glance/assets/ledger.json` +
  `/home/lumen/telemetry/intervention_ledger.json` (the wq#744 seam — Mission
  Control v2 can read it from there when a tile is wanted; MC app.py untouched).
- Surface: **Glance "Intervention Ledger" widget**, top of the full column —
  headline number with ↓/↑ (green/red), parked-on-Frank, undetected minutes,
  8-week trend rows. glance.yml backed up
  (`glance.yml.bak.*_pre_r4_ledger`), container restarted clean.
- Units: `lumen-intervention-ledger.{service,timer}` enabled; first run proven
  (exit 0, JSON emitted).

## CAVEATS (do not oversell this)

1. Pre-W34 weeks are undercounts; treat W34 as week zero. Trend is meaningful
   from W35 onward.
2. Regex classification has noise both ways. The fix is cultural, not code:
   use the markers. Every session that watches Frank intervene and does not
   write a marker is re-blinding the instrument.
3. (d) has no automatic sensor yet — it depends on `DOWNTIME-UNDETECTED-MIN=`
   being recorded at incident post-mortem. A real sensor would diff
   product-liveness signals against aq alert timestamps; that is a follow-up,
   seeded as a wq ticket.
4. W35 reading 0 through Wednesday is genuinely encouraging but three days of
   post-autonomy-directive calm is not a trend.

## ACCEPTANCE

Frank looks at Glance: one number, colored, "down = fleet needs Frank less",
last 8 weeks under it. Met.


---

## ADDENDUM 2026-08-27 — VERIFICATION + PARTIAL-WEEK TREND FIX (session wq925-r4-8030d2)

The 2026-08-26 build (above) was verified to failure this session, not rebuilt. The ticket
(wq#925) had never been claimed/closed, so it was treated as unverified until proven.

**Verified live (product-liveness, not trust):** daily timer ran clean 06:15Z today; DB
populated; JSON emitted atomically; the Glance widget actually renders (confirmed via
`/api/pages/lumen-fleet/content/`, not just the asset file existing); W34=13 baseline honest.

**Defect fixed (grade A, reproduced live):** `emit()` drove headline/trend/color from the
CURRENT PARTIAL week vs a FULL prior week. On a Thursday it emitted `headline 0, delta -13,
trend down` (green ↓) — structurally over-optimistic Mon–Wed, decaying as the week fills. A
lying trend is the DELIVERY_FAILURE Case-5 class (a display that lies costs trust every view).
Fix: headline/trend/color = last COMPLETE week vs the week before (now **13, +2, RED ↑**,
labelled "last full week"); the current week rides along as `in_progress` with a **same-weekday
pace** line (`pace_vs_prev` = events-so-far minus prior week's events through the same weekday)
that never drives the color. New JSON keys: `in_progress{week,dow,events_to_date,pace_vs_prev,
prev_through_same_day,compared_to,parked_last,undetected_min}`; `week`/`current` now name the
last complete week. Design: `foreman/ops/R4_partialweek_fix_design.md`. Backups:
`intervention_ledger.py.bak.*_pre_partialweek_fix`, `glance.yml.bak.*_pre_r4_partialweek`.
Edge-tested on a temp DB copy (synthetic partial event can't hijack the headline; Monday
baseline uses only the prior Monday); prod DB left clean; Glance restarted clean, no template
errors, new layout renders.

**Defect found, NOT auto-fixed (ticketed):** the precision channels this report calls "the
high-precision channel going forward" are inert — nobody writes the `FRANK-*` markers, and the
other structured source (gated `frank_response`) was retired by the 2026-08-22 autonomy
directive. The collector therefore runs on the conservative regex fallback, which undercounts
(exactly caveat 2). "Use the markers" is a fix that lives in humans' hands = not a fix.
→ **wq#1014** (P2): auto-emit `FRANK-*` markers at intervention time via commit hook / classifier,
so the instrument stops depending on memory. → **wq#1015** (P3): build a real automatic sensor
for metric (d) undetected-downtime (diff product-liveness forward-progress vs aq alert
timestamps) — today (d) only lands via a manual post-mortem marker.

**Rejected (verified before assuming):** mining `decisions/*.md` wholesale as an automated
Frank-signal — the recent files there are agent-authored design docs, so counting them would
INFLATE the metric the ledger exists to shrink. Not added.

## ADDENDUM 2026-08-30 — CHAT-ORIGIN MINING (wq#1014, session wq1014-a3)

Caveat 2's class defect (annotation that depends on memory) is fixed at the source the
memory was standing in for: the collector now mines Frank's own messages from Claude
session transcripts (`~/.claude/projects`), where every intervention lands by
construction — messenger-bridged or console-typed — with headless worker/cron sessions
excluded by sentinel and project-dir heuristics. Events land as `conf='chat'`, one per
(session, kind, day); a same-(kind, day) `regex` event is suppressed at rollup so one
incident is not counted twice (chat-only suppression: W31–W34 history, incl. the W34=13
baseline, is bit-identical). `FRANK-*` markers remain parsed as an optional precision
bonus, no longer load-bearing. New `mine-chat --dry-run` subcommand for live
calibration. Design: `foreman/ops/R4_frank_marker_autoemit_design.md`; tests:
`foreman/ops/tests/test_intervention_ledger_chat.py` (10, green). Metric (d) still has
no automatic sensor — that remains wq#1015.

Net: R4 is deployed, verified, and now shows a number Frank can trust. The honest reading today
is **W34 = 13, up 2 on W33 (red)** — last full week the fleet needed Frank *more*; W35 is pacing
one better so far, shown without a false green arrow.
