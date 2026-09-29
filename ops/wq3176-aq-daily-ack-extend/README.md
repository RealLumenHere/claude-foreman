# wq#3176 — extend the daily aq auto-ack rule [audit parent wq#3162]

622 open aq rows at audit time, dominated by `Failed unit: X` / `Recovered: X`
twins that never get paired off and by recurring info-class sources the existing
daily auto-ack (`/home/lumen/agents/alert-queue/aq_autoack.py`) can't reach
because it only acks `count=1` (fired-once) rows. Live snapshot at authoring
time: 91 open `failed-unit` rows, 79 of them `count>1` -- structurally invisible
to the existing rule.

`aq_daily_ack_extend.py` adds three rules against the same `alerts.db`
`aq.py`/`aq_autoack.py` use:

1. **Recovered/Failed pair ack** — `source='failed-unit'` posts two independent
   open rows keyed `Failed unit: <unit>` / `Recovered: <unit>` (see
   `ops/r4_undetected_sensors.py`'s `_token_for` doc — it never transitions a
   single row to resolved). When `Recovered: <unit>`'s `last_ts` is newer than
   the matching `Failed unit: <unit>`'s, the unit is back up: ack both.
2. **Stale info-class ack (recurring included)** — same info tier
   (`min/low/default/1/2/3`, never `4/5/high`) and 7-day staleness window as
   `aq_autoack.py`'s existing rule, but without the `count=1` restriction:
   `last_ts` age alone already proves the row stopped firing.
3. **Per-source display cap** — any source with more open rows than the cap
   (default 15) keeps only its N most-recently-active rows open; older overflow
   is auto-acked. Catches sources like `jay-watch` that are neither info-class
   nor a Failed/Recovered pair but still bury real rows under duplicate history.

All three ack (never resolve/delete) with an audit note, same idempotent
convention as `aq_autoack.py` and `ops/wq1649-aq-source-escalate`.

Not yet wired to a systemd timer: `aq_autoack.py` lives at
`/home/lumen/agents/alert-queue`, outside this repo, so (same as wq#1649) this
ticket ships the responder logic here. Follow-up: call `run()` (or inline the
three rules) from `aq_autoack.py`'s own `run()`, on `lumen-aq-autoack.timer`'s
existing daily cadence.
