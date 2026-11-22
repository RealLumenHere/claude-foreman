# wq#5300 — verify of wq#5259 (PR #2728, commit f9fc0146): CONFIRMED

Verifier: fresh instance, adversarial pass. Diff read cold:
`ops/wq2092-cert-watch/install.sh`, `ops/wq2092-cert-watch/README.md`,
`ops/tests/test_wq5259_cert_watch_intended_off.py`.

## Claim under test
`lumen-cert-watch.timer` is intended-off since the 2026-10-08 subtraction, so
`install.sh --verify` must stop asserting it enabled/active, and `install.sh`
must not `enable --now` it. Gated by `CERT_WATCH_ON=0`
(`ops/wq2092-cert-watch/install.sh:13`, verify gate `:40-44`, install gate `:55-61`).

## Evidence
1. Intent is real. `/home/lumen/scratch/subtraction/timers-disable-phase2.txt:14`
   lists `lumen-cert-watch.timer`; `timer-massacre-phase2.md` §C "Disabled (58)"
   line 63 names it under security/guard detectors. Not in
   `timers-keep-phase2.txt` or `timers-reenable-phase2.txt`. No ticket after
   wq#3648 asks to re-enable it (`wq list --status done | grep -i cert`: only
   wq#2092/2093/2983/3000/3008/3014, all before 2026-10-08, plus wq#5259).
2. Live target: `liveness.ops_verified('wq2092-cert-watch')` →
   `('SATISFIED', 'wq2092-cert-watch: VERIFY OK')`.
3. PR tests: `pytest ops/tests/test_wq5259_cert_watch_intended_off.py` → 6 passed.
4. Probe that can fail (copy of the bundle + `_verify.sh` in
   `/tmp/fworker-wq5300/probe/ops`, live host):
   - `CERT_WATCH_ON=0` → rc 0, VERIFY OK.
   - `CERT_WATCH_ON=1` → rc 1, MISS not active / not enabled / not scheduled
     (SubState=dead). The gate controls the timer checks; it does not hide them for good.
   - `CERT_WATCH_ON=0` with a one-line drift appended to the shipped
     `cert_watch.py` → rc 1, `MISS differs: /home/lumen/services/lumen-cert-watch/cert_watch.py`.
     The non-timer checks still fire, so the verify was not hollowed out.
5. Install path: `systemctl enable --now` occurs once, inside the flag-1 branch
   (`install.sh:55-56`). A root run with the shipped flag copies files and runs
   daemon-reload, and leaves the timer alone. The subtraction can no longer be undone by accident.

## Findings (non-contradicting)
- **wq#5299 is moot.** It was filed at 09:53:35, when wq#5259 closed, because the
  evidence citation pointed at a worktree path. It asks for a root install. The live
  `--verify` is already OK and a root install would change nothing about the
  timer. It should be closed as no-op. It is not re-implemented here.
- Coverage residue: with the timer off, nothing found in the readable dirs
  watches TLS expiry (no gatus `CERTIFICATE_EXPIRATION` condition under
  `/home/lumen/agents` or `/home/lumen/minerva`). That gap came from the
  subtraction policy, not from this diff. Re-enabling means flipping `CERT_WATCH_ON=1` (README:41-48).

## Verdict
CONFIRMED. No contradiction, so no escalation.
