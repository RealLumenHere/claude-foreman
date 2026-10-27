# wq#4695 — stop watchdog + maint-ops canary harnesses from targeting retired nodes

> **wq#4701 (2026-10-27): the watchdog half of this bundle is retired.**
> `lumen_watchdog.py` had not run on a schedule since 2026-08-27 (wq#970 disabled its
> timer when Gatus took over its checks), and Gatus covers every target it pinged. The bundle
> copy was dropped and `install.sh --verify` no longer checks it. The script and units are
> removed by `ops/wq4701-lumen-watchdog-retire/`. The last shipped copy is
> `git show 7e91411f:ops/wq4695-watchdog-retired-canary-a1/lumen_watchdog.py`. The canary
> half below (`fleet-retired-nodes.json`, `tests/*_canary.sh`) still stands. Deploy steps 2 and
> 8 and the watchdog liveness check are obsolete.

Found by `build-litellm-wadjet-e77ef4` (wq#4680). `/home/lumen/agent/lumen_watchdog.py`'s
`DEVICES` dict still pinged `isis`, `hathor`, `khepri` (retired 2026-10-22) and `wadjet`
(retired 2026-10-26); only `nephthys` had ever been removed (wq#589 precedent). None of the
four were in `TRANSIENT_DEVICES`, so each retired node going permanently dark would have
alerted exactly like a real outage. Separately, `/home/lumen/bin/maint-ops/tests/ollama_canary.sh`
and `tailscale_canary.sh` hardcoded `wadjet` (LAN `192.168.8.173`) as THE canary node for the
ollama-upgrade / tailscale-upgrade test-to-failure harnesses, so both break once wadjet is
powered down for good.

**Verified before changing anything:** read `lumen_watchdog.py` end to end
(`lumen_watchdog.py:140-151` in the original) — `isis`/`hathor`/`khepri`/`wadjet` were NOT in
`TRANSIENT_DEVICES`, so a `not up and prev_up` transition on any of them does send a `high`
priority ntfy page (`send_ntfy(..., priority="high")`), confirming the ticket's "can alert"
claim rather than assuming it.

## Replacement canary: anubis

Checked candidates against `lumen_state.py current <node>` (the authoritative live-fact
store) rather than guessing from role names alone — this caught two traps a role-only read
would have missed:

- `apep` ("DEPLOYABLE TEST STATION" — sounds ideal) is actually **OFFLINE** since ~2026-09-13
  (`apep.status`, aq `storage:apep:unreach` firing).
- `thoth` looks idle in a point-in-time snapshot but carries an explicit, dated retraction:
  `thoth.host_fitness = "RETRACTED as services-host candidate: ... Prior 'idle/viable' reading
  was a between-load snapshot. Keep torrent-only."` — a prior worker already learned this the
  hard way; reusing it as a canary would repeat that mistake.
- `seshat` was *also* just retired (`seshat.lifecycle = "retired"`, since 2026-10-26 — the same
  day as wadjet).
- `sekhmet` is DOWN since 2026-10-11 (16+ days stale as of this ticket).
- `maat`/`sobek`/`bes` are mid-flight in the vault HA migration
  (`design/vault_ha_migration_off_n100.md`, landed this same day) — all three are actively
  serving PG/Vaultwarden/HA roles today, explicitly not idle.
- `gameboy` runs the Lumen-Plays-Pokemon kiosk 24/7 on fragile, peripheral-stripped hardware
  (Surface Pro 6, BT/audio/camera disabled) — wrong shape for a test-to-failure harness.
- `neith` is an active Commander MCP standby-pool member (`neith.status = "SERVING"`,
  observed taking real traffic 2026-10-07) — touching its tailscaled risks the failover path.

**`anubis`** (Dell OptiPlex 3090, 32GB RAM, LAN `192.168.8.232`, TS `100.121.17.5`, ssh user
`anubis`) has no such flag: `lumen_state` shows it ONLINE with facts verified 20-31 days ago,
no retraction, no DOWN/OFFLINE status, no `lifecycle=retired`. Its role (Borg backup repo +
Gitea) is a real workload, not literally idle, but every throwaway unit in these harnesses
already lives in its own prefix/port/systemd-unit (never touches the prod payload), and a
brief tailscaled blip during a scheduled canary run is a bounded, acceptable cost against the
alternative of a harness permanently pointed at a dead host. `anubis@192.168.8.232` /
`anubis@100.121.17.5` is the established ssh target used elsewhere in this repo (e.g.
`ops/wq3717-a2-backup-important-fix/backup-important.sh`, `ops/wq2607-dc-endpoint-anubis/`).

## One shared retired-node list

`fleet-retired-nodes.json` (deploys to `/home/lumen/bin/fleet-retired-nodes.json`) is now the
single place that records a node retirement. Both consumers read it:

- `lumen_watchdog.py::check_no_retired_devices_monitored()` — startup guard: raises loudly if
  any key in `DEVICES` is also in this file, so a retired node silently re-added (or a
  retirement recorded here but never reflected in `DEVICES`) pages immediately instead of
  resuming silent pings of a dead host.
- `ollama_canary.sh` / `tailscale_canary.sh`'s `check_canary_not_retired()` — refuses to run
  (`exit 9`, clear message) if the configured canary node name is in this file.

`DEVICES` itself still just drops the four retired entries with dated comments, same style as
wq#589's `nephthys` removal — the shared file is the drift guard, not a dynamic filter, so the
dict stays easy to read at a glance.

## Fail loud, not skip

Both canary scripts now do an explicit `ssh -n ... true` reachability probe before anything
else (before `slock acquire`, before `setup()`): an unreachable canary now prints one
unambiguous `REFUSED: canary node ... is unreachable — this harness FAILS, it does not skip`
line and exits 9, instead of running 20+ seconds into `setup()` and exiting on a generic
`SETUP FAILED`, or (if some caller treats early-vs-late failure differently) being read as
"nothing to test here."

## Components

| File | Deploy target | Purpose |
|---|---|---|
| `fleet-retired-nodes.json` | `/home/lumen/bin/fleet-retired-nodes.json` | shared retired-node list, read by both consumers below |
| `lumen_watchdog.py` | `/home/lumen/agent/lumen_watchdog.py` | removes isis/hathor/khepri/wadjet from `DEVICES`; adds the startup drift guard |
| `tests/ollama_canary.sh` | `/home/lumen/bin/maint-ops/tests/ollama_canary.sh` | wadjet → anubis; retired-check + reachability pre-flight |
| `tests/tailscale_canary.sh` | `/home/lumen/bin/maint-ops/tests/tailscale_canary.sh` | wadjet → anubis; retired-check + reachability pre-flight |

## Why the live files aren't touched directly

Same convention as wq#897/wq#911/wq#948/wq#1295/wq#1406/wq#1590/wq#2627/wq#2904 (see their
READMEs) and enforced live by `harness/guard.sh`'s primary-tree wall (wq#900/wq#1719): this
BUILD worker's sandbox cannot write `/home/lumen/agent/lumen_watchdog.py` or
`/home/lumen/bin/...` — both are `PRIMARY_TREES` entries, confirmed this session by a direct
`Edit` attempt on `/home/lumen/agent/lumen_watchdog.py`, blocked with `guard.sh: BLOCKED —
Write/Edit inside PRIMARY tree /home/lumen/agent denied (wq#1719/wq#900)`. This bundle ships
the real file contents in-repo (so the fix is reviewable and citable against this branch's
diff) and leaves the actual deploy to an attended/privileged path.

## Deploy checklist (operator)

1. `cp ops/wq4695-watchdog-retired-canary-a1/fleet-retired-nodes.json /home/lumen/bin/fleet-retired-nodes.json`
2. `cp ops/wq4695-watchdog-retired-canary-a1/lumen_watchdog.py /home/lumen/agent/lumen_watchdog.py`
3. `cp ops/wq4695-watchdog-retired-canary-a1/tests/ollama_canary.sh /home/lumen/bin/maint-ops/tests/ollama_canary.sh`
4. `cp ops/wq4695-watchdog-retired-canary-a1/tests/tailscale_canary.sh /home/lumen/bin/maint-ops/tests/tailscale_canary.sh`
5. `chmod +x /home/lumen/bin/maint-ops/tests/ollama_canary.sh /home/lumen/bin/maint-ops/tests/tailscale_canary.sh`
6. Commit the two canary-script changes in the `/home/lumen/bin` repo (separate repo from
   this one; not reachable from this worktree).
7. `bash ops/wq4695-watchdog-retired-canary-a1/install.sh --verify` — expect `VERIFY OK`.
8. **Acceptance probe:** `python3 /home/lumen/agent/lumen_watchdog.py` once, by hand — expect
   no `RuntimeError` from `check_no_retired_devices_monitored()` and a normal
   `devices_down=[...]` summary line with no `isis`/`hathor`/`khepri`/`wadjet` key in
   `HOMELAB_STATUS.json`. Re-running `/home/lumen/bin/maint-ops/tests/tailscale_canary.sh`
   against `anubis` (off Frank's normal maintenance cadence) is the regression probe for the
   canary half.

## Liveness check (from the ticket)

```
! grep -qE "^[[:space:]]+\"(isis|hathor|khepri|wadjet)\":" /home/lumen/agent/lumen_watchdog.py
```

Passes once step 2 above lands — `lumen_watchdog.py` in this bundle has no such line
(see `lumen_watchdog.py:30-37`).
