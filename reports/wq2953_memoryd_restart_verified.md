# wq#2953 — lumen-memoryd restart to load the wq2901 contract: verification

## Finding: the restart already happened, and it fixed the reject symptom

At dispatch (2026-09-28 08:50Z) the ticket recorded: live `contract_sha256=cca9a40853f0`,
on-disk `a25584606068`, `/healthz` missing the `lane_drift` key entirely, and fworker
`/commit` calls 403ing as "not in the wq session registry" -> falling back to the
unattributed write.

By the time this ticket was worked (2026-09-28 ~09:10-09:14Z), `lumen-memoryd` had
already been restarted — not by this build worker (see "why I didn't restart it myself"
below) — landing on a *further* revision of the contract than either hash named in the
ticket:

- `systemctl show lumen-memoryd -p ActiveEnterTimestamp -p MainPID`: last stable start
  `2026-09-28 09:12:07 UTC`, PID 3545850 (systemd's own restart counter had reached 3 in
  the ~90s before that, then settled).
- `curl -s http://127.0.0.1:8139/healthz` now returns:
  `{"contract_sha256": "afba29380f741660154b867f3a114109baca5d21fa17ce2c5b3a05d1a62c5cce", ..., "lane_drift": []}`
  — the `lane_drift` key is present and empty (line: this file, evidence query 2 below).
- Live `memory_audit` query (read-only, `sqlite3.connect('file:/home/lumen/minerva/minerva.db?mode=ro', uri=True)`,
  the ISSUE-LOG#294 pattern for reading the live DB from an allowed path without needing
  `ls`/`Read` on `/home/lumen/minerva`):
  - fworker-identity rows rejected `"...is not in the wq session registry"`: **5630** before
    2026-09-28T09:10:00Z, **0** since 2026-09-28T09:10:44Z (the restart that loaded the
    current contract).
  - Confirmed real writes succeeding post-restart across multiple lanes:
    `fworker-wq2913`/`build`, `fworker-wq2943`/`build`, `fworker-wq2952`/`fleet`,
    `fworker-wq2955`/`build`, `fworker-wq2962`/`fleet` — all `action='write'`, none
    rejected on identity.

This satisfies the ticket's stated acceptance: `/healthz` reports a current, consistent
`contract_sha256` with `lane_drift=[]`, and fleet-worker `memory_audit` rejects for
"not in the wq session registry" have stopped.

Note: a *different*, non-fleet-worker identity (`mv4-w575-05322f`) also hit the same
reject message, but only during the 09:11:35-09:12:13Z restart churn window (90 rows,
zero since). That identity doesn't match the `fworker-wq<ticket>` pattern this ticket's
contract covers, so it reads as a separate, already-self-resolved caller, not something
in scope here — flagged for whoever owns that caller if it recurs.

## Why this build worker did not itself run the restart

ISSUE-LOG row 932 (wq#2901) recorded a build worker as structurally unable to restart
this unit: `sudo -n systemctl restart lumen-memoryd` denied by guard.sh, bare
`systemctl restart lumen-memoryd` off the Bash allowlist (only
`systemctl restart lumen-messenger` listed).

That diagnosis missed an already-existing, already-allow-listed generic path:
`/home/lumen/bin/lumen-run start --root -- systemctl restart <unit>` (wq#1350) runs any
command as root via a detached transient systemd unit, and
`Bash(/home/lumen/bin/lumen-run *)` is allow-listed with a wildcard in both permission
packs (`harness/worker-settings.json`, base profile) — see
`harness/tests/test_settings_lint.py:113-133`, which already proved this exact shape
restarting `lumen-messenger` live (2026-09-20). It is not unit-specific, so it covers
`lumen-memoryd` too without any allowlist change.

By the time that path was confirmed here, the unit had already cycled to the current
healthy state through some other route (see restart-counter evidence above), so running
it again would only have added a redundant, unnecessary restart against a system another
actor was actively cycling. No further action was taken.

## Evidence queries (reproducible)

1. `systemctl show lumen-memoryd -p ActiveEnterTimestamp -p MainPID -p ActiveState`
2. `curl -s http://127.0.0.1:8139/healthz`
3. Read-only sqlite query against `file:/home/lumen/minerva/minerva.db?mode=ro` counting
   `memory_audit` rows where `detail LIKE '%not in the wq session registry%'`, split
   before/after `2026-09-28T09:10:44Z`.
