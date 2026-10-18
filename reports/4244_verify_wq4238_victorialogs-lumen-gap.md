# wq#4244 verify of wq#4238 (Loki+Promtail eval, PR 2161): PARTIAL CONTRADICT

Verifier: design-s-a21137 (claude-sonnet-5.5), 2026-10-18. Probes re-run cold against Bes VictoriaLogs (100.110.156.91:9428).

## Verdict
- CONFIRMED: "do not deploy Loki+Promtail". VictoriaLogs is live, ~707 MB storage (745.7 MB now), 417 GB free on Bes.
- CONTRADICTED: "VictoriaLogs ships logs from all 8 fleet nodes right now". The claim rests on ALL-TIME per-host counts.
  A `_time:10m` query returns 7 hosts: anubis, khepri, isis, thoth, sobek, maat, bes. `lumen` (22.2M entries, the largest
  source) is absent. hathor is absent too, expected (auto-sleep node).
- Minor: resident memory measured ~329 MB now vs ~107 MB in the eval. Still far under Loki sizing, conclusion unaffected.

## Evidence
- Newest `host:lumen` entry in VictoriaLogs: 2026-10-08T01:02:46Z (10 days stale).
- `vlogs-forwarder.timer` on Lumen: disabled, inactive; cursor file mtime 2026-10-08 01:02; last service run shipped 1313 lines.
- Cause is DELIBERATE, not a fault: `scratch/subtraction/REPORT.md` line 70 lists vlogs-forwarder under "Other (2)" disabled in
  subtraction phase 2 ("journald keeps the logs"); `phase2-results.txt` line 86 shows it inactive/disabled.
- `lumen_state` still says `lumen.vlogs_forwarder = vlogs-forwarder.timer 60s ... NOW` and is tagged no-probe, so the stale fact
  and the 10-day gap were both invisible. The rollout bundle `--verify` returns rc=2 on `unreachable: hathor` (sleeping node),
  so it cannot serve as a liveness probe either.

## Impact and options (Frank decision, not actioned)
Lumen is the host most often debugged, so cross-node search currently misses it. journald still holds the 10 days
(3.26M lines since the cursor, journal 2 GB of 4 GB cap). Re-enable is safe mechanically: forward-journal.sh drains in
2000-line POSTs, 50k/tick, cursor saved per chunk, ~65 ticks (~1 h), Bes has headroom. Reversing it undoes a subtraction decision.
- Re-enable: `sudo systemctl enable --now vlogs-forwarder.timer` (undo: `disable --now`).
- Or accept the gap and correct lumen_state + the eval wording. Either way fix the stale `lumen.vlogs_forwarder` fact.
