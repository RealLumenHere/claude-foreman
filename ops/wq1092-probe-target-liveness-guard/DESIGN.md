# wq#1092 — sentinel probe outlives retired service (class fix)

## Root cause

`sentinel_probes.py` probes that watch a systemd unit (`probe_a5_health`,
`probe_pokemon_liveness`, `probe_litellm_auth_monitor`, and the per-unit loop
in `probe_state_drift`) call `systemctl is-active`/`show` directly and treat
every non-`active` result the same way: FAIL, and if the probe is marked
critical, an urgent ntfy page. There is no distinction between "the unit
exists and is down" (a real incident) and "the unit doesn't exist to check"
(a stale probe — the target was retired and nobody updated/removed the
probe). wq#969 folded `probe-a5-health` in from a standalone timer without
checking its three targets (`lumen-planner/generator/evaluator`) still
existed; they'd been retired 8 days earlier. The probe was born permanently
FAIL, and once wq#965's escalation responder went live it auto-paged Frank
with a P0 (aq#899, 47 repeats over ~22h) for a condition that was never
recoverable by definition — instance root cause + fix in
`agents/work-queue/reports/1064_a5_probe_retire.md`.

## Class analysis

Any future probe that names a systemd unit, container, or host can suffer
the identical failure mode the moment that target is retired:
- wq#126 (`probe_dead_references`/`probe-dead-references`) audits SQL
  tables, LiteLLM models, and a hardcoded ping list — it does not touch
  systemd units, so it never covered a5-health. Not a duplicate of this fix.
- wq#723 (`retirement-audit`) sweeps `docs/fleet/retired_services.yaml`
  weekly to catch a retired service that's still ALIVE — the opposite
  direction. It does not reconcile against what sentinel_probes.py is
  watching, so it would not have caught a5-health either (a5-health isn't
  in that YAML, and even if it were, retirement-audit doesn't look at
  probe code at all).
- Nothing in the codebase currently distinguishes "unit retired" from "unit
  down" for probes that shell out to systemctl. That's the gap this ticket
  closes.

## Chosen approach

A shared classifier, `_unit_load_state(unit)`, and a shared guard,
`_stale_or_none(unit, probe_name)`, used by every probe that checks a
systemd unit directly:

- `LoadState` `not-found` (unit file removed — the a5-health shape) or
  `masked` (deliberately disabled, e.g. lumen-agent) means the target no
  longer exists to be watched. The guard returns a STALE marker instead of
  letting the probe fall through to its normal is-active/ActiveState logic.
- STALE is folded into the probe's existing 3-tuple return contract as
  `(True, "STALE TARGET: ...", False)` — `ok=True`, `critical=False` — so it
  is structurally impossible for a stale target to reach the FAIL path or
  `ntfy_urgent`, regardless of what the probe's own critical flag would
  otherwise be. This matches the ticket's "make the class impossible, not
  this instance" ask: any *future* probe author who runs a systemd unit
  through this same guard function gets the safety for free.
- STALE also files exactly one build-lane wq ticket per (probe, unit) pair,
  deduped via the probe's existing `state.json` (same file `probe-output-rate`
  etc. already use for cadence gating), so the condition surfaces for a human
  to actually remove/retarget the probe instead of silently passing forever.
  One ticket, not a page, and not a re-file on every 30-min cycle.
- Kept OUT of scope (ticket calls it a bonus): reconciling retirement-audit's
  YAML against sentinel_probes.py in the same run. The per-probe guard below
  already makes the failure mode structurally impossible without that
  cross-file wiring, and retirement-audit's existing scope (services, not
  probes) is a separate concern wq#723 already owns.

## Where it's wired in

- `probe_a5_health` — re-enabled (was commented out by the wq#1064 instance
  fix) and now goes through the guard for all three A5_SERVICES entries
  first. Live proof that guarding is *sufficient* to leave a probe watching
  retired targets enabled without it ever paging again — this is the
  test-to-failure case (see below): it now returns STALE, not CRITICAL.
- `probe_pokemon_liveness`, `probe_litellm_auth_monitor` — single-unit
  guard call before the existing ActiveState logic.
- `probe_state_drift` — per-unit loop now classifies each unit; STALE units
  are reported in the detail string but do not add to `problems` (so they
  can't flip the probe to FAIL).

## Test-to-failure

`bin/tests/test_probe_target_liveness.py` (new, offline, no real wq/systemctl):
1. Monkeypatch `subprocess.run` to return `LoadState=not-found` for a fake
   unit → guard returns STALE, not a FAIL tuple.
2. Monkeypatch to return `LoadState=masked` → same.
3. Monkeypatch to return `LoadState=loaded` + `ActiveState=active` → guard
   returns None (probe proceeds to its own logic unchanged) — proves the
   guard doesn't paper over an unrelated genuine-down case.
4. Ticket-filing dedup: two calls for the same (probe, unit) with a shared
   scratch `state.json` file exactly one `wq new` subprocess call.
5. Full `probe_a5_health()` run with all three A5_SERVICES mocked
   not-found → returns `(True, ..., False)` — the literal aq#899 shape,
   now inert.
All assertions pass offline; see PR for the run transcript.

## Rollback

`sentinel_probes.py.bak.<ts>_pre_wq1092` alongside the file (same convention
as every prior sentinel_probes.py change). Reverting drops the guard;
`probe_a5_health` would need to be re-commented by hand since re-enabling it
is bundled with this change (rollback instructions restate this explicitly
in the file comment).
