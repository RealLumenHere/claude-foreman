# wq#3585 — root-apply wq3354-fleet-selfheal, verify-confirm (class 8 exposure census, wq#3514)

Author: claude-sonnet-5 (build worker, worktree `wq3585-a1`).

## Ticket text vs. live state

wq#3585 was dispatched against the same `install.sh --verify` failure wq#3514's own
closure report quoted (4 `MISS` lines: `exposure_census.py` absent, the two
`lumen-exposure-census.{service,timer}` unit files absent, and the timer not enabled).

That gap no longer exists. wq#3584 (ticket number lower than this one, landed earlier as
commit `80a50b13`, report
`reports/wq3584_root-apply_wq3354-fleet-selfheal_exposure-census.md`) already ran the
privileged install — `lumen-run start --root -- bash
ops/wq3354-fleet-selfheal/install.sh` — installed all five units plus the ten `CODE[]`
files, merged the `exposure:` block into the live `selfheal.yaml`, and enabled+started
`lumen-exposure-census.timer`. wq#3585 is a duplicate-ticket dispatch on the identical
spec item (same root-cause class as kb/ISSUE-LOG.md issue #73: two ticket numbers covering
one spec item with no machine-readable link between them); logged as kb/ISSUE-LOG.md
row 1209.

## Live re-verify (this ticket's own evidence, not a rerun of wq3584's)

`/home/lumen/bin/lumen-run start --name wq3585verify -- bash
/home/lumen/foreman/ops/wq3354-fleet-selfheal/install.sh --verify`
(unit `lumen-job-wq3585verify-1791379923`), read back via `journalctl -u
lumen-job-wq3585verify-1791379923.service --no-pager -o cat`:

```
VERIFY OK
LUMEN_JOB_EXIT=0
```

This covers every check in the `--verify` branch of
`ops/wq3354-fleet-selfheal/install.sh:31-43`: code parity for all ten `CODE[]` files
(`:28-29,33`), `selfheal.yaml` presence (`:34`), all five `UNITS[]` files byte-matching
under `/etc/systemd/system` (`:26-27,35`), and `lumen-fleet-selfheal.timer`,
`lumen-leakwatch.service`, `lumen-exposure-census.timer` all enabled+active (`:36-41`) —
including the exact three `lumen-exposure-census.*` checks the ticket's failure quote
named as missing.

## Verdict

No root-apply action was needed under this ticket — the install wq#3585 asked for was
already live from wq#3584. Liveness gate `ops_verified(wq3354-fleet-selfheal)` is
satisfied by the fresh `VERIFY OK` above, run under this ticket's own worktree/session,
not carried over from wq#3584's report.
