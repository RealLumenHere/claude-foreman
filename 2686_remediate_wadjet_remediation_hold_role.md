# wq#2686 — remediate wadjet `remediation_hold`/`role` facts (live-offline contradiction)

Both `wadjet.remediation_hold` and `wadjet.role` were stamped 2026-09-24 reading
as confirmed-online ("Khepri+Wadjet online. Normal operation" /
"Wadjet coming online now per Frank"), written from Frank's stated plan and
never checked against a live probe. `tailscale status` run live 2026-09-25
13:32 UTC shows wadjet genuinely offline (node key expired, last seen 68d
ago) — the same check method confirmed khepri reachable in the same
fact-set, ruling out a stale-cache explanation. The older `wadjet.availability`
fact (also 2026-09-24, conf 0.93) already correctly flagged this
unreachability but was left standing uncontradicted next to the two
optimistic facts.

`tailscale`/`ssh` probes are not on this worker's allowlist
(harness/worker-settings.json; see kb/ISSUE-LOG.md #71 for the same gap on a
prior ticket), so this session relied on the ticket's own live tailscale
check plus the pre-existing `wadjet.availability` fact (conf 0.93, same
2026-09-24 date, independently observed) as corroboration rather than
re-running the probe itself.

## Fix

Superseded both facts via `lumen_state.py ingest` (source-kind
`agent_written`, conf 0.90) so `current wadjet` no longer reads as confirmed
online without cross-referencing tailscale by hand:

- `wadjet.remediation_hold` → now states the hold is off per Frank's plan but
  was **never live-verified**, cites the 2026-09-25 tailscale check, and
  points at `wadjet.availability` as corroboration.
- `wadjet.role` → keeps the target role (LLM inference node) but marks it
  **not yet active** for the same reason.

Verified post-ingest via:

```
python3 /home/lumen/minerva/lumen_state.py current wadjet
```

Both predicates now show `(since 2026-09-25)` with the corrected, hedged
wording; `wadjet.availability` is unchanged and remains consistent with the
correction. `FLEET_MAP.md` was already correct (`[DEPLOYED-BUT-OFFLINE]`,
per wq#2634) and needed no change.

## Scope

State-store facts only — no code change. This doc is the citable record of
the ingest calls for wq#2686's acceptance gate.
