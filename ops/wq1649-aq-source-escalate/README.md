# wq#1649 — aq escalation beyond source=probe; spam-source suppression

Source: `research/FLEET_SERVICE_ATLAS.md` §5 SPOF #2 (wq#1627). `bin/aq_escalate.py`
(wq#965) only ticketizes `source='probe'` rows (plus the dispatcher-stall title
match, wq#1410). 250 open aq rows at audit time (84 `ntfy:lumen-alerts`, 78
`jay-watch`, 37 `failed-unit`) sit entirely outside that scope, and
`ops/alert_hygiene_audit.py` only looks at the backlog quarterly.

`aq_source_escalate.py` adds three rules against the same `alerts.db`:

1. **Failed-unit per-unit ticket** — open `source='failed-unit'` rows grouped by
   unit, count >= 3 -> one `fleet-fires` ticket per unit.
2. **Spam-source suppression** — any source with >=50 open rows -> one "source is
   spamming" ticket, then every open row from that source is marked
   `owner='aq-spam-suppressed'`.
3. **Finding-exit autoack** — open failed-unit rows whose unit is listed in
   `guard-finding-exit.list` (the wq#1657 finding-exit contract) are auto-acked
   instead of ticketed; runs first so that noise never inflates rules 1/2.

Not yet wired to a systemd timer — intended cadence matches `lumen-aq-escalate.timer`
(10m). Wiring + the actual `guard-finding-exit.list` population is a follow-up once
wq#1657 lands its classifier.
