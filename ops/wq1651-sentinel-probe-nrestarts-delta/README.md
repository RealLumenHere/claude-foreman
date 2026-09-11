# wq#1651: sentinel probe: NRestarts delta on fleet daemons

**Finding: already shipped.** The ask — "NRestarts delta > K in 24h -> aq row
edge-triggered" for `Restart=always` daemons with no `StartLimitBurst` ceiling
(atlas §5 #7, litellm-auth-monitor NRestarts=8 / powerdash-vmtunnel NRestarts=6) —
is implemented by `wq#1704`'s `cmd_restarts()` in
`ops/wq1704-missing-watchdogs/fleet_spof_watch.py:302-336` (commit `acd11d0`,
already on this branch's history).

That function:
- enumerates every fleet daemon via `fleet_daemon_units()` and keeps only units
  with `Restart in ("always", "on-failure")` (`fleet_spof_watch.py:305-309`) —
  a superset of the two example units named in this ticket, not just those two.
- persists a rolling baseline `NRestarts` count per unit across a
  `RESTART_WINDOW_S` window, defaulting to `86400` (24h) —
  `fleet_spof_watch.py:85,310-315`.
- raises an aq row via `raise_finding()` when `delta >= RESTART_DELTA`
  (default `5`) — `fleet_spof_watch.py:318-329` — and edge-clears it back to
  `resolved` the next pass the delta drops below threshold via
  `clear_finding()` — `fleet_spof_watch.py:330-331`. Both `raise_finding`/
  `clear_finding` operate on `alerts.db` through `aq` (`fleet_spof_watch.py:106-127`),
  matching the "aq row edge-triggered" requirement verbatim.

This ticket asked for the probe specifically inside `sentinel_probes.py` (the
separate cron-driven probe framework at `/home/lumen/sentinel/probes/`, see
e.g. `ops/wq1092-probe-target-liveness-guard/sentinel_probes.diff`), whereas
wq#1704 shipped it as a subcommand of a new standalone watchdog script instead.
Adding a second, parallel NRestarts-delta implementation into
`sentinel_probes.py` would duplicate monitoring of the same signal (same
`systemctl show ... NRestarts` data, same 24h window, same aq-row shape) and
produce double alerts/double edge-clears racing each other for the same unit
on two different cadences. That is a regression, not a fix, so no duplicate
probe was added.

No code change needed beyond this record. See `kb/ISSUE-LOG.md` #312.
