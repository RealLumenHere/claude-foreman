# wq#1095 — probe-a5-health targets retired unit lumen-planner (not-found)

This ticket is the expected, one-time artifact of the wq#1092 stale-target
guard doing exactly what it was built to do: `probe-a5-health` (in
`/home/lumen/sentinel/probes/sentinel_probes.py`) checks `lumen-planner`,
`lumen-generator`, `lumen-evaluator` — all retired 2026-08-21 and never
coming back — via `_stale_or_none()`. Hitting `LoadState=not-found` made
`_stale_or_none()` return `STALE` (ok=True, critical=False) instead of
`CRITICAL`, and filed this build-lane ticket once (dedup'd by
`_file_stale_probe_ticket`'s `filed[key]` guard — it will not refile).

Per wq#1092's own PR notes: *"probe-a5-health re-enabled+guarded, live-proven
inert on real host (filed wq#1095)"* — this ticket **is** that live proof.
The guard worked correctly; there is no incident here (Tier-0 rule 7 also
applies in spirit: the "problem" is a value wq#1092 deliberately introduced
to prove the fix, not a fleet fault).

## What changed

Proof obtained, so there's no remaining reason to keep running a check that
can only ever return the same `STALE TARGET: ... (build ticket filed, never
pages)` string every 30 minutes forever. Commented out the
`("probe-a5-health", probe_a5_health, False)` registration in the `PROBES`
list (see `sentinel_probes.diff`) with a comment explaining why, referencing
both wq#1092 (why it was live) and wq#1095 (why it's now off). Left
`probe_a5_health()`, `A5_SERVICES`, `A5_STATE`, etc. defined but unused —
cheap to resurrect if A5 (or a similar guard-proof need) ever comes back;
deleting ~50 lines of working, documented code for a one-line registration
change isn't worth the loss of history.

Verified: `python3 -m py_compile` clean; full module import clean via
`importlib.util`; confirmed `probe-a5-health` no longer appears in the
imported module's `PROBES` list (19 probes remain, was 20) and
`probe_a5_health` is still a callable attribute on the module.

Target file lives outside git (`/home/lumen/sentinel` has no `.git`) and was
edited in place directly, per this repo's established convention for
non-git infra files — `sentinel_probes.py.bak.20260830_005345_pre_wq1095`
sits alongside it. `sentinel_probes.diff` here is the reviewable record of
that edit, not something to `git apply` — the change is already live.

## Not done, and deliberately so

Did not touch `A5_SERVICES`/`A5_STATE`/the redis dead-rate logic inside
`probe_a5_health()` itself, and did not remove the function. The ticket's
own boilerplate says "comment out **or** retarget" — commenting out the
registration is the minimal, reversible action that stops the pointless
recurring `systemctl` calls without discarding a working, documented probe
body that a future ticket might want to retarget onto whatever succeeded A5.
