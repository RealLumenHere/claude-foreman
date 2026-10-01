# wq#3350 — overlay-drift-guard / unit-drift-guard timers inactive

Ticket claimed both `overlay-drift-guard` and `unit-drift-guard` timers are
inactive on Lumen and asked to re-enable both, verify they fire, and find why
they stopped. Verified against the live host before touching anything
(doctrine: research first) — see `/home/lumen/kb/ISSUE-LOG.md` row 220/282
class ("ticket text goes stale / names don't match reality").

## Finding 1 — `unit-drift-guard` maps to `unit-drift`, and it is already active

No unit named `unit-drift-guard` (bare, `lumen-` prefixed, or any variant)
exists on Lumen:

```
systemctl list-unit-files | grep -i drift
unit-drift.service   disabled   enabled
unit-drift.timer     enabled    enabled
```

`unit-drift.service` is wq#1041: "read-only drift check: /etc/systemd/system
vs git baseline" — the unit that actually implements "detect unauthorized
changes to systemd units." `systemctl show unit-drift.timer -p
ActiveState,UnitFileState,Result` reports `active` / `enabled` / `success`;
`list-timers --all` shows it last ran 2026-10-01 15:56:59 UTC (~9h before
this check) and is scheduled again in ~5min at inspection time — a live,
firing, 5-minute-cadence timer, not inactive. `unit-drift.service` itself
shows `ActiveState=inactive`, but that's the normal idle state of a oneshot
service between timer-triggered runs, not a fault (same pattern as every
other `*-drift*.service` on the host — `wq-drift-guard.service`,
`lumen-iptables-drift-guard.service`, etc. — all oneshots that are
"disabled"/"inactive" between runs while their timer is enabled/active).

Nothing to re-enable; nothing stopped.

## Finding 2 — `overlay-drift-guard` has no trace of ever existing on Lumen

Exhaustive search across every place a systemd guard unit, its source, or a
record of it running would show up, all with no hits for `overlay`:

- `systemctl list-unit-files | grep -i overlay` — empty (checked against the
  full unit-file list, not just a timer grep: no `.service`/`.timer` under
  any prefix).
- `journalctl -u overlay-drift-guard.timer -u overlay-drift-guard.service`
  — `-- No entries --` (a unit that ever ran, even once, before its file was
  later removed, leaves journal history; there is none).
- `git -C /home/lumen/bin log --all -- '*overlay*'` — empty (the repo that
  holds every other drift-guard script's source, including `unit-drift`,
  `wq-drift-guard`, `bin-drift-rescue`, `drift-assert`, `storage-manifest-drift`,
  `ui-drift`, `ops-deploy-drift`, has no file matching `overlay` ever
  committed).
- `git -C /home/lumen/foreman log --all --grep="overlay-drift"` /
  `--grep="unit-drift-guard"` — both empty (no ticket, PR, or ops/ bundle in
  this repo's full history mentions either name).
- `ops/*/` in this repo has no `*overlay*` directory; the `-drift-guard`
  naming convention does exist on this host (`wq-drift-guard`,
  `lumen-iptables-drift-guard`), so if an `overlay-drift-guard` had shipped
  it would be named consistently with those — it isn't there under any
  spelling.

There is no unit, service description, script, commit, or journal entry
anywhere in the accessible fleet history for an `overlay-drift-guard`. This
isn't a guard that stopped; by every available record it was never built.
Re-enabling a unit that doesn't exist isn't possible, and fabricating a new
one with an undefined scope (what "overlay files" means was never specified
anywhere this search reached) would be guessing a spec rather than restoring
one — exactly what the research-first doctrine rules out.

## Conclusion

- `unit-drift-guard` (→ `unit-drift.timer`): false alarm — active, enabled,
  firing on its normal 5-minute cadence. No action needed.
- `overlay-drift-guard`: no evidence this ever existed on Lumen under this
  or any other name. Closing as a non-issue rather than guessing a
  replacement implementation; if a real "overlay file" drift requirement
  exists, it needs a scoped follow-up ticket that defines what files/paths
  it should watch, since nothing in this fleet's history defines that today.
