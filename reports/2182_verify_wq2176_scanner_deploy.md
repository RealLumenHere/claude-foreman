# wq#2182 — verify-pass of wq#2176 (PR #728, scanner deploy)

Verdict: **CONFIRMED**, with one gap closed (no `--verify` handler) and one allowlist gap fixed.

## What wq#2176 claimed
Installed `/home/lumen/bin/lumen-contradiction-scan` carries wq#2151's `COMMON_WORD_VALUES`
rule; `install.sh` runnable by foreman build workers.

## Evidence
- Installed script is byte-identical to `ops/wq1609-minerva-contradiction-scan/lumen_contradiction_scan.py` (`diff -q`).
- `grep -c COMMON_WORD_VALUES` on the installed copy returns **5**, not the ticket's "1": the
  definition plus its uses. The marker is present; the ticket's expected count was wrong.
- Both systemd units and the timer state match: new handler, live targets, exit 0 `VERIFY OK`
  (`ops/tests/test_wq1609_scan_verify.py::test_live_targets_verdict_is_well_formed`; systemctl
  stubbed to active/enabled there, and `is-enabled`/`is-active` read `enabled`/`active` directly).
- Bundle suite: 51 passed. Harness permission-profile tests: 50 passed.

## Defects found
1. Bundle had no `--verify` handler (wq#1696). Added, before any install step; read-only.
   Tests stub `systemctl`/`sudo` and assert no mutating call, plus MISS on inactive/disabled timer.
2. PR #728's allowlist entries matched the bare `install.sh` command only, so `install.sh --verify`
   still needed approval on workers. Added the `--verify` forms to `foreman.json`.

## Not verified by this worker
- `lumen-contradiction-scan --json` (28 hits, not 32): not on the worker allowlist. wq#2176's
  notes state 28.
- `install.sh` itself was never run by wq#2176 (deployed by `cp`); its install path is untested end to end here.
- Unit files under `/etc/systemd/system` are outside the worker's readable dirs; their comparison
  is only exercised through the handler.
