# wq#3925: nephthys AER correctable-error watch, boot 2026-10-14T22:23Z

## Method

Read-only `journalctl -k -b -o short-iso` over ssh to nephthys, counted lines
matching `AER: Multiple Correctable error message received` on root port
`00:01.0`, bucketed by UTC hour. No config change, no power-cycle action
taken.

## Result (24h since boot, as of 2026-10-15T02:xxZ)

| UTC hour | AER count |
|---|---|
| 22 | 8 |
| 23 | 8 |
| 00 | 24 |
| 01 | 41 |
| 02 | 47 |

Monotonically rising across all 5 observed hours.

## Comparison vs wq#2776 baselines (ops/wq2851-aer-storm-alarm/README.md)

- Healthy boot (boot 0): 8 total, max 5/hr.
- Boots that preceded a power loss: 33-1986/hr, with boot -3 rising to 69/hr
  in its final hour before the drop.
- This boot's current trajectory (8/8/24/41/47) tracks the pre-drop boot -3
  shape, not the healthy boot 0 shape, though it has not yet crossed the
  69/hr floor those boots hit right before dying.
- Live 15-minute window count via `ops/wq2851-aer-storm-alarm/aer_storm_alarm.py
  check` was 17, below the alarm's `raise_above=30` (aer_storm_alarm.yaml:11),
  so the wq#2851 notify-only alarm has not fired.

## Verdict

Still climbing, not yet over the 69/hr pre-drop floor. Per the ticket's own
decision rule ("if >69/hr or still climbing, tell Frank power loss is likely
again, keep idle, do not dispatch local-model workers"), the "still
climbing" branch applies now even though the "> 69/hr" branch does not yet.

Decision (full-autonomy, logged here and on wq#2851/wq#2852 via `wq note`):
treat nephthys as at risk of a repeat idle power loss; keep it idle and do
not dispatch local-model or build workers to it until the hourly AER count
turns over and clears to within the boot-0 healthy ceiling (roughly
single-digit per hour) for at least two consecutive hours.

## Cross-references

- wq#2851 (ops/wq2851-aer-storm-alarm/): the leading-indicator alarm this
  watch leans on for the 15-minute window check and the 69/hr and 5/hr
  reference points.
- wq#2852: nephthys's gpu-lowpower@card1 SIGTERM-at-boot ticket, which
  already suspected the AER activity on 00:01.0 as a possible trigger — noted
  the recurrence there for continuity.
- ISSUE-LOG row 1326: `wq note` denial caused by a denylisted verb literal
  appearing in descriptive note prose, not an actual command.
