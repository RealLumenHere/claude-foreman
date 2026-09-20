# wq#2194 — verify of wq#1782 (PR #747): CONFIRMED

Verifier: claude-sonnet-5@high, fresh instance. Scope: scanner fix in
`ops/wq1609-minerva-contradiction-scan/lumen_contradiction_scan.py`
(`build_dead_value_index`, commit d2bc710) and the report's claim that no doc was wrong.

## Probes

| check | result |
|---|---|
| `pytest ops/wq1609-minerva-contradiction-scan/test_lumen_contradiction_scan.py` | 54 passed |
| baseline: pre-fix scanner (`git show d2bc710^:...`) live read-only scan | 30 hits, incl. gameboy/nephthys `ts_ip` at LIVE_FLEET_STATE.md:18,26, gameboy.yaml:9, FLEET_MAP.md:24,279, claude_memory.md:4212,4215 — the ticket's count reproduces |
| treatment: fixed scanner, same DB, same files | 20 hits; every `ts_ip` hit gone |
| old-vs-new `build_dead_value_index` diff on the live DB | 95 -> 93 entries; exactly 2 suppressed: `gameboy.ts_ip` 100.118.56.59 and `nephthys.ts_ip` 100.98.74.87. Each is held open by the same subject's `tailscale_ip` with an identical value. No collateral suppression |
| ticket-1782 premise check | the closed `ts_ip` facts have no same-predicate successor, so the live address was indexed as dead; the report's diagnosis holds |
| `wq#2192` (verifier clobber split-out) | exists, status open |

The three new tests cover the rename rescue, the same-subject scope (a different
subject holding the value does not rescue), and bare labels (`active`) not rescued.

## Residual hits match the report's verdicts

khepri.power_plug P1 (false positive), pbox.status x2 in claude_memory.md
(real, proposal-only), isis.service_state (verifier garbage), plus archival_notes
rows. Counts differ from the report (18) only because `state_facts` moved since.

## Non-blocking observations

- The rescue uses `value_re.search` over every open value of the subject, so a
  prose open fact that merely quotes the old address would also rescue it. The
  existing same-predicate check has the same behavior; live impact is 0 (only the
  two intended entries were suppressed).
- `tailscale status` was not on the worker allowlist, so the report's
  independent tailscale cross-check was not re-run; the `state_facts` open
  `tailscale_ip` rows were checked instead.
- Two commits with the same message and diff exist (350aa11 on the feature branch,
  d2bc710 on main); only d2bc710 is in main, so nothing is applied twice.
