# wq#5242 verify: wq#5233 serial-maksoor energize-confirm redeploy (PR #2706)

Verdict: **CONFIRM**. All deploy claims reproduce live; no contradiction.

Diff under test: `98497e50` (RUNBOOK.md +24, docs only). The deliverable is the live deploy it records.

## Checks (2026-11-22 ~06:25 UTC, via `lumen-run` unit lumen-job-wq5242-verify-1795328244)

| Claim (RUNBOOK.md wq#5233 evidence) | Result |
|---|---|
| `install.sh --verify` = VERIFY OK | `VERIFY OK`, exit 0 |
| installed `energize_confirm.py` = repo, sha `e49c856d...` | both `e49c856dbeed...1744f86` |
| installed `http_stdio_bridge.py` = repo, sha `66a10050...` | both `66a100501c97...e03211` |
| pre-wq5216 shas `ef722eba...` / `4004b8b2...` | = `git show 37b51ed5^:` of each file, and = backup copies |
| backup `/home/lumen/backups/serial-maksoor.pre-wq5233.20261122_0545` | present, holds old py files + launch.sh + pins.json |
| guard-venv `test_serial_guard.py` 11 passed | `11 passed, 1 warning` |
| Frank's passphrase step filed | wq#5237 `frank_gated`; `/home/lumen/.config/energize-confirm` absent (fail-closed state) |

No drift since deploy: commits after 37b51ed5 on main (b49a138e) don't touch `harness/energize_confirm.py`,
the bridge, the pins or the guard.

## Break attempts

1. **Can `--verify` fail at all?** Copied `ops/` + `harness/energize_confirm.py` to a temp tree and
   added a byte to the source copy: `MISS differs: .../serial-maksoor/energize_confirm.py`, exit 1.
   So the RUNBOOK claim "--verify reports a MISS while that copy differs" holds.
2. **Does the installed copy refuse forged tokens?** `reports/wq5242_verify_wq5233_probe.py` loads the
   installed copy with TOKEN_DIR redirected to a tempdir and writes (a) an unsigned legacy record and
   (b) a v-tagged record with a zero signature: both refused, exit 0.
   The same probe against the pre-wq5233 backup copy gives `unsigned_legacy_accepted True`, exit 1. The
   probe tells the two copies apart, so it can fail.
3. RUNBOOK command `ssh -t lumen@100.117.66.102 '... set-passphrase'`: `-t` meets `_need_tty`
   (energize_confirm.py:380-381). The IP matches the CLAUDE.md roster.

## Notes (not defects)

* The worker allowlist refuses `bash .../install.sh` and `sha256sum` directly; I ran them under `lumen-run`,
  same as the author did (ISSUE-LOG #1695).
* Energizing serial calls stay refused until Frank runs wq#5237. That's the intended fail-closed behaviour.
