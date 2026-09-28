# wq#2093 — verify-pass of wq#2092 (lumen-cert-watch): CONFIRMED

Archetype 8 adversarial verify-pass, attempt 3. Author claude-sonnet-5; verifier
claude-sonnet-5@high, fresh instance. Two earlier verify rounds under this same
ticket id already contradicted and were fixed on `main`:

- wq#2983 (ISSUE-LOG #452): unreachable run dropped `tier`/`aq_id`, re-paging
  standing alerts and orphaning aq rows on renewal — fixed at `a9cc0e15`,
  independently confirmed CONFIRMED in `reports/wq3000_verify_wq2983_cert_host_tier_aqid.md`.
- wq#3008 (ISSUE-LOG #981): a malformed `targets.json` raised uncaught and
  aborted the entire run before the 28 cloudflared hosts were checked — fixed
  at `fe149d1e82` (`6b829258` on this branch).

The ticket's stale text points at PR#683 (404 on gitea now — renumbered).
`wq show 2092` gives the real merge: PR#1279, merged `70d85d2195`. Current
`ops/wq2092-cert-watch/` on `main` = `01d42dfd` (feat) + `791a2c5e` (--verify
handler) + `a9cc0e15` (wq#2983 fix) + `6b829258` (wq#3008 fix).

## What I did to try to break it

1. **Read `cert_watch.py` cold**, full file (333 lines): target discovery
   (`read_cloudflared_hosts`, `load_extra_targets`), `fetch_not_after`
   (deliberately `CERT_NONE` to read an expired/self-signed leaf, decoded via
   `ssl._ssl._test_decode_cert` — a private CPython API, but present and
   working on the deployed Python 3.14.4), the tier state machine in
   `check_host`, and `run()`'s target aggregation/dedup.

2. **Traced the tier transition table by hand** for every prev-state x
   outcome combination (cold start, standing tier, escalation, de-escalation
   without hitting `ok`, full renewal, outage then same tier, outage then
   renewal, outage then escalation, flapping). Two hypotheses looked like
   bugs on first read; both falsified by follow-up probes:

   - **Partial de-escalation (7d → 21d, never touching `ok`) never fires a
     "renewed" notice and never calls `aq_resolve`.** Probe
     (`/tmp/fworker-wq2093/probe_deescalate.py`): `check_host` on a 5d→15d
     improvement returns `aq_id` unchanged, no ntfy. Confirmed true, but it
     is **not a defect** — the cert is still inside the 21d warning window,
     so a standing-but-improved aq row staying open matches the documented
     "only a move to a more severe tier alerts" contract exactly
     (`test_standing_tier_does_not_realert`'s intent generalizes here).

   - **Escalation through/without an outage (e.g. 7d aq_id=10 → expired)
     calls `aq_ingest` again without ever calling `aq_resolve(10)`, so the
     old row looks orphaned.** This is the same gap `reports/wq3000_verify_wq2983_cert_host_tier_aqid.md`
     flagged as "out-of-scope pre-existing gap ... not escalating" without
     checking the actual dedup mechanics. I checked: `aq.ingest()`
     (`_runs/wq2092/snapshots/20260928T093552Z-home_lumen_agents_alert-queue_aq.py:34-47`)
     is an **upsert** — `SELECT ... WHERE source=? AND key=? AND status='open'`,
     and if a row is found it **bumps that same row** (count/last_ts/message)
     instead of inserting a new one; only when no open row exists does it
     insert. Since `key=host:port` never changes for a host and the prior
     alert's row is still `status='open'`, a second `aq_ingest` on escalation
     returns the **same** row id, just with the title/message/priority
     refreshed to the new tier. My first probe
     (`/tmp/fworker-wq2093/probe_escalation_leak.py`,
     `/tmp/fworker-wq2093/probe_escalation_leak2.py`) mocked `aq_ingest` to
     return a fixed new id regardless of key, which manufactured a leak that
     does not exist against the real `aq.ingest` upsert semantics. No bug.

3. **Ran the shipped suite**: `python3 -m pytest test_cert_watch.py -q` →
   **31 passed** (18 base + 8 `MalformedTargetsTests` from wq#3008 + the
   wq#2983 unreachable-carries-forward set), 0 failures.

4. **Liveness — the ticket's `--verify` requirement.** `install.sh` already
   has the wq#1696 handler (added by wq#791a2c5e, deepened by wq#3008 to
   parse `targets.json` with the shipped loader instead of grepping it):
   ```
   lumen-run start --name wq2093-verify -- bash /home/lumen/foreman/ops/wq2092-cert-watch/install.sh --verify
   lumen-run wait lumen-job-wq2093-verify-1790603750.service   # DONE:0
   lumen-run log  lumen-job-wq2093-verify-1790603750.service   # VERIFY OK
   ```

5. **Live dry-run against the deployed copy** (not the worktree):
   ```
   lumen-run start --name wq2093-dryrun -- python3 /home/lumen/services/lumen-cert-watch/cert_watch.py run --dry-run --state /tmp/fworker-wq2093/dryrun-state.json
   ```
   `DONE:0`, 28 `cert-watch: <host> tier=ok days_left=72.05` lines, no errors.

6. **Real production evidence, not just my dry-run**:
   `systemctl status lumen-cert-watch.timer` → `active (waiting)`, enabled,
   last trigger today 2026-09-28 09:38:29, next in 19h (`Persistent=true`,
   `OnBootSec=10min`, `OnUnitActiveSec=24h`). `systemctl status
   lumen-cert-watch.service` → last run `Main PID 41439 (code=exited,
   status=0/SUCCESS)`, journal shows all 28 hosts `tier=ok days_left=72.23`.
   The timer already ran for real today, not just under my probe.

7. **ISSUE-LOG.md #452/#981 cross-check**: both entries marked `solved`,
   consistent with the merged fixes on `main`.

## Verdict: CONFIRMED, no contradiction

wq#2092 as it stands on `main` (with the wq#2983 and wq#3008 follow-up fixes)
does what it claims: discovers 28 cloudflared-fronted hosts plus an
operator-edited `targets.json` extra list, alerts once per severity
escalation via a deduped `aq` row + ntfy, resolves cleanly on full renewal,
survives a malformed `targets.json` and an unreachable host without losing
state, and the `--verify` handler answers the wq#1696 contract read-only and
passes live (`VERIFY OK`, exit 0). The timer is active, enabled, and has
already run successfully in production today. Two hypothesized new defects
(partial de-escalation, escalation-through-outage aq leak) were both
falsified against real code/real aq semantics, not just asserted away.

## Evidence commands (reproducible)

```
git -C /home/lumen/foreman log --oneline -- ops/wq2092-cert-watch/
python3 -m pytest /home/lumen/foreman/ops/wq2092-cert-watch/test_cert_watch.py -q
lumen-run start --name wq2093-verify -- bash /home/lumen/foreman/ops/wq2092-cert-watch/install.sh --verify
lumen-run wait <unit>; lumen-run log <unit>   # VERIFY OK
lumen-run start --name wq2093-dryrun -- python3 /home/lumen/services/lumen-cert-watch/cert_watch.py run --dry-run --state /tmp/fworker-wq2093/dryrun-state.json
systemctl status lumen-cert-watch.timer lumen-cert-watch.service --no-pager
python3 /tmp/fworker-wq2093/probe_deescalate.py
python3 /tmp/fworker-wq2093/probe_escalation_leak.py
python3 /tmp/fworker-wq2093/probe_escalation_leak2.py
```
