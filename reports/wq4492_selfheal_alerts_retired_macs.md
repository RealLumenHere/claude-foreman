# wq#4492 — selfheal/alert audit for retired Macs (isis, hathor, nephthys, khepri)

READ-ONLY per ticket scope: `aq list open`, `ops/wq3354-fleet-selfheal/selfheal.yaml`,
`lumen_state.py current <node>`. No selfheal_merge/live-config edit made (that needs
backup + precheck, out of scope here).

## Retirement confirmed

`lumen_state.py current <node>` shows all four carrying the docs-s2 lifecycle fact:

```
isis.lifecycle      = retired  (since 2026-10-22)
hathor.lifecycle    = retired  (since 2026-10-22)
nephthys.lifecycle  = retired  (since 2026-10-22)
khepri.lifecycle    = retired  (since 2026-10-22)
```

## selfheal has no lifecycle awareness — the gate is still `nodes.ignore`

`ops/wq3354-fleet-selfheal/selfheal.yaml:79-92` (`nodes.ignore`) lists only
`thor, maksoor, apis, pbox, seshat, be3600, 3dprinter`. None of isis/hathor/nephthys/khepri
are in it. `hathor` only appears in `nodes.sleepers` (selfheal.yaml:93-104), which
suppresses the self-idle poweroff nap, not a genuine hard outage.

`sh_nodes.py:134-135` reads `ignore`/`sleepers` straight from those two YAML lists, and
`sh_nodes.py:170-186` branches purely on set-membership in them — there is no
`lifecycle` field read anywhere in `sh_nodes.py`. Setting `lumen_state` lifecycle=retired
has **zero** effect on whether selfheal pages a node; only a `nodes.ignore` edit does.
This is exactly what SESSION-BRIEFING (patch-s-18583b) warned: the live-config edit
"is not yet made," so selfheal keeps treating all four as live fleet nodes.

The existing `nodes.notes` entries for isis (selfheal.yaml:111-125), nephthys
(:134), and khepri (:167-178) each explicitly instruct "never nodes.ignore / stays a
real page" — written before the 2026-10-22 retirement decision, now stale against it.
`nephthys.lifecycle` note text in lumen_state (`nephthys.monitoring`) still says
"lifecycle active 2026-09-20 ... supersedes the decommissioned marker," which is itself
now stale against the new 2026-10-22 `retired` value.

## Open alerts still targeting the four (aq list open, snapshot 2026-10-23)

| node | alert key | id | count | last fired |
|---|---|---|---|---|
| isis | `fleet-selfheal node-offline:isis` | 2671630 | x453 | 2026-10-20T06:38:34Z |
| isis | `ntfy:lumen-alerts` repeat pages ("offline for NNN min") | 2672079-2672092 (12 rows) | x1 each | up to 2026-10-20T06:38:34Z |
| khepri | `fleet-selfheal node-offline:khepri` | 2671632 | x1 | 2026-10-19T22:00:43Z |
| khepri | `ntfy:lumen-alerts fleet-exec: khepri unreachable` | 2672093 | x1 | 2026-10-20T07:04:41Z |
| nephthys | `fleet-selfheal node-offline:nephthys` | 2671539 | x1 | 2026-10-18T18:03:15Z |
| hathor | `ntfy:lumen-alerts fleet-exec: hathor unreachable` | 2672094 | x1 | 2026-10-20T07:05:17Z |

No `node-offline:hathor` fleet-selfheal key is open (consistent with hathor's
`nodes.sleepers` membership suppressing the plain tailnet-absence check), but the
general `fleet-exec: hathor unreachable` ntfy alert is still open and unrelated to the
sleeper suppression — so hathor is not fully quiet either.

## Conclusion

All four retired Macs still have live, unresolved selfheal/alert entries. Root cause:
`nodes.ignore` was never updated to match the 2026-10-22 retirement — confirming the
SESSION-BRIEFING warning. Recommended follow-up (not done here): add
isis/hathor/nephthys/khepri to `ops/wq3354-fleet-selfheal/selfheal.yaml` `nodes.ignore`
with a backup of the file and a precheck that no still-wanted signal (e.g. hathor's
wake-race note, khepri's live-inference note) is silently dropped, then `wq ack`/close
the open alert ids listed above.

## Evidence

- `lumen_state.py current isis|hathor|nephthys|khepri` — `*.lifecycle = retired (since 2026-10-22)` for all four.
- `ops/wq3354-fleet-selfheal/selfheal.yaml:79-92` — `nodes.ignore` list, none of the four present.
- `ops/wq3354-fleet-selfheal/selfheal.yaml:93-104` — `nodes.sleepers`, hathor only.
- `ops/wq3354-fleet-selfheal/sh_nodes.py:134-135,170-186` — ignore/sleepers set-membership logic, no lifecycle read.
- `python3 /home/lumen/bin/aq list open` — alert rows cited in the table above.
