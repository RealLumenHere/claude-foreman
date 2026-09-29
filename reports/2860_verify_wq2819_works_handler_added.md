# wq#2860 verify of wq#2819 (PR #1174): WORKS; verify handler was missing and is added here

Verifier: final-push-ad230d, 2026-09-29.

Live on Lumen:
- installed script, service and timer are byte-identical to `ops/wq2819-iptables-drift-guard/`
- timer enabled, firing every 15 min; last run `OK: persisted == live`
- journal since 2026-09-28: 144 OK ticks, 12 DRIFT events (all dockerd raw/DOCKER rules for container publishes,
  including the litellm :4001 canary), each reconciled on the same tick
- `test_iptables_drift_guard.py`: 8 passed

Findings (none is a defect against the stated scope):
1. No `--verify` handler, so the bundle read as ABSENT to ops-deploy-drift. Added in this PR;
   `install.sh --verify` on Lumen now prints VERIFY OK.
2. v4 only. `iptables-save` is diffed, so v6 drift alone (e.g. wq#3089's lockdown) is invisible.
   `netfilter-persistent save` does write rules.v6 as a side effect of a v4 save.
3. It persists whatever is live at the tick, including rules inside a dead-man window (wq#2808 T+200s,
   wq#3089 180s). A tick landing there saves the pre-rollback state; the next tick re-saves after rollback.
   Window is ~15 min, so only a reboot in that window would matter.
