# wq#1800 — storage_manifest.yaml / FLEET_MAP.md drift triage

Raised by `storage-manifest-drift` (wq#1615) on 2026-09-11, fingerprint
`e17ca13bc0d479d6`. Three findings, triaged one by one against the Minerva store
and the live map.

## storage_manifest.yaml — 0 findings

`storage_manifest.diff` is empty (0 bytes) and the survey-regenerated candidate
differs from the committed manifest by 0 lines. Nothing to reconcile. Survey
coverage was 8/17 nodes (5 unreachable, 3 parked, 1 no_address) — a coverage
fact the report already prints, not drift.

## be3600 `lan_ip` — real, fixed

`/home/lumen/FLEET_MAP.md:22` carried the placeholder `VERIFY` in the lan_ip
column. The store has held the value since 2026-09-10:

    be3600 | lan_ip | 192.168.0.1 (grade B: apep's default gateway + ARP
    a8:6e:84:ad:0f:14, 2026-09-10; not read from the router itself)

Corroborated by apep's own row (`192.168.0.161`) — apep sits behind this router,
so the router's LAN gateway address is the same /24. Applied by `apply.py` as
`192.168.0.1 (grade B)`; the grade rides in the cell because the value is
inferred from a downstream host, not read off the router.

`apply.py` runs via `lumen-run start -- python3 apply.py` (as lumen, the file's
owner): FLEET_MAP.md sits at `/home/lumen` root, outside every worktree and
`--add-dir` root — ISSUE-LOG #322. It backs the file up, edits exactly one cell,
and is a no-op on a second run.

Left alone deliberately: the same row's status cell reads
`[DEPLOYED] active, unmonitored` while the store says `status OFFLINE`
(2026-09-10). That is status drift across the `/home/lumen` docs, which wq#1782
already owns; the drift checker does not check `status`.

## p12 `lan_ip` — false positive, detector fixed

`p12` is an Athom smart plug, not a node. It has no roster row; the only match
in FLEET_MAP.md is line 271, inside a prose plug list
(`p1 khepri + p12 wadjet unmonitored`). Its open `status` fact is `SUPERSEDED`
(2026-08-07), and its `lan_ip` fact stays open forever because nothing
supersedes a fact about hardware that is gone. Writing `192.168.8.111` into the
map would have been wrong.

Fix in `../wq1615-storage-manifest-drift/storage_manifest_drift.py`: skip
subjects whose newest open `status` starts with one of
retired/removed/superseded/decommissioned/torn-down. `OFFLINE` and `inactive`
are excluded from that list on purpose — be3600 is OFFLINE and still belongs on
the roster, and suppressing it would have hidden the one real finding here.

## fleet_map_stale — real signal, structurally always-on, detector fixed

The check compared the file's mtime to `max(valid_from)` over the *entire*
store. The newest open fact was `fleet-workstream frank_todo_order` (2026-09-20)
— a todo ordering the map never carries. Any store write at all made the map
"stale", so at a 2-day threshold it could never clear. Scoped the recency query
to the `tailscale_ip`/`lan_ip` predicates the check already reconciles, so the
lag now means "the map predates a fact it is supposed to carry".

That is exactly the state it was in: newest roster fact 2026-09-11T02:55:16Z vs
mtime 2026-09-08T05:46:08Z (+2.9d) — driven by be3600's own lan_ip. The finding
was right; the measurement was not.

## Verification

    $ python3 -m pytest ops/wq1615-storage-manifest-drift/ -q
    29 passed

Live, after `apply.py` (`check_fleet_map` against the real map and store):

| detector | lag | findings |
|---|---|---|
| patched (this PR) | -9.0d | *(clean)* |
| installed (stock) | -0.2d | `p12 fleet_map_value_missing lan_ip 192.168.8.111` |

The stock detector's -0.2d is only the edit's own mtime bump — it refires in two
days on unrelated store churn. The patched one is clean because the map now
leads every roster fact in the store.
