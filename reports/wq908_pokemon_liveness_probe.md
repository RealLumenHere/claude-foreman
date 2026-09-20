# wq#908 — lumen-pokemon-live: liveness, not process-alive

During the 2026-08-23 12h freeze, `systemctl` reported `lumen-pokemon-live.service`
ActiveState=active the entire time. The process was alive, blocked on a brain call at
0.0% CPU — no turn was landing. Nothing watched for that; Frank caught it by looking at
the screen. `ActiveState=active` only proves the PID exists, not that turns are moving.

## Fix

Added `probe_pokemon_liveness()` to the existing Fleet Sentinel Tier-2 probe runner
(`/home/lumen/sentinel/probes/sentinel_probes.py`, run by `sentinel-probes.timer` every
30 min, PROBES list gate=False so it runs every cycle). This is the same alerting path
already used by every other fleet health check (ntfy urgent + aq triage queue + Gatus
dashboard/heartbeat) — no new infrastructure, no new timer.

The probe reads the same signal `lpp-pstack` already surfaces interactively (age since
the last `turn_summary` event in the newest `live-agent-*.jsonl` under
`/home/lumen/pokemon/events/`) but runs it unattended on a schedule and actually fires an
alert instead of waiting for a human to look:

1. `systemctl show ... ActiveState` != `active` → FAIL, critical (service down/crashed,
   distinct failure mode from "active but frozen").
2. No `live-agent-*.jsonl` file at all while active → FAIL, critical (can't verify
   liveness).
3. Fresh run, no `turn_summary` yet: OK for the first 10 min (start-up grace), then FAIL
   critical if still nothing.
4. `turn_age` (now − last `turn_summary` timestamp) > 45 min while ActiveState=active →
   FAIL, critical → `ntfy_urgent` fires immediately. This is the wq#908 shape: active,
   but not progressing.
5. `turn_age` > 20 min → FAIL, non-critical (queued to `aq` for triage, no urgent page).
6. Otherwise OK.

Thresholds are set against the actual turn cadence: the live run-summary at the time of
this fix averaged ~195s/turn (10 turns / 1955s wall). 45 min is ~14x that average, so a
single missed cycle won't false-page on a slow battle turn, but the fleet finds out
within one sentinel cycle (30 min) instead of 12 hours.

Also registered `probe-pokemon-liveness` as a Gatus external-endpoint in
`/home/lumen/sentinel/config/config.yaml` (`5-probes` group, 50 min heartbeat dead-man
switch — same pattern as every other probe, e.g. `probe-storage-integrity`), so the probe
itself silently stopping running is also caught, not just the thing it checks.

## Verification

Unit-tested the age-threshold branches against synthetic event data (systemctl mocked
active, `POKEMON_EVENTS` pointed at a temp dir):

```
fresh (1m gap):     (True,  'last turn 1.0m ago', False)
warn (25m gap):     (False, 'no turns in 25m (ActiveState=active)', False)
critical (50m gap): (False, 'NO TURNS in 50m though ActiveState=active — process alive
                              but not progressing (wq#908 freeze shape)', True)
```

Ran the probe against live host state (no mocking): `lumen-pokemon-live.service` was
stopped at the time (unrelated, a prior step in this session's investigation stopped it),
and the probe correctly caught that too:

```
(False, 'lumen-pokemon-live.service ActiveState=inactive/dead', True)
```

`py_compile` clean; full `sentinel_probes.py` module import clean;
`probe-pokemon-liveness` present in `PROBES` (13 probes total, was 12); config.yaml
parses and `probe-pokemon-liveness` present in `external-endpoints` (29 total, was 28).

Did not run `sentinel_probes.py main()` end-to-end against production — that would push
real Gatus/aq/ntfy state for every probe in the file, not just the new one, which is out
of scope for a verification step. The new probe's function was exercised directly and in
isolation.

## Residual: Gatus needs a restart

`sentinel-gatus` (docker, no hot config reload) needs a restart to register the new
external-endpoint for dashboard visibility and its own heartbeat dead-man switch. This
build worker's guard hook blocks all `docker` subcommands except `ps`/`logs`, so it
cannot do this restart. **The primary fix does not depend on this**: `sentinel-probes.timer`
re-execs `sentinel_probes.py` fresh every 30 min (`Type=oneshot`), so the new probe and
its direct `ntfy_urgent` alert path are live on the very next cycle with no restart
required anywhere. The Gatus registration is the secondary/dashboard layer only.

Action needed from Frank: `docker restart sentinel-gatus` (or let it pick up on its next
natural restart) to light up the dashboard tile and heartbeat watch for this probe.

## Diff — `/home/lumen/sentinel/probes/sentinel_probes.py`

```diff
@@ imports @@
 import json, os, re, sqlite3, subprocess, time, urllib.request
 from collections import Counter
+from datetime import datetime
 from pathlib import Path

@@ after probe_storage_integrity(), before PROBES @@
+POKEMON_EVENTS = Path("/home/lumen/pokemon/events")
+POKEMON_START_GRACE_S = 10 * 60   # a fresh run hasn't landed a turn yet -- not a stall
+POKEMON_STALL_WARN_S = 20 * 60
+POKEMON_STALL_CRIT_S = 45 * 60
+
+def probe_pokemon_liveness():
+    """wq#908: lumen-pokemon-live read ActiveState=active through a 12h freeze —
+    the process was sitting at 0.0% CPU blocked on a brain call the whole time.
+    'active' only proves the PID exists, not that turns are landing, so liveness
+    here is measured independently: wall-clock age since the last turn_summary
+    event actually written to the live event log."""
+    try:
+        r = subprocess.run(
+            ["systemctl", "show", "lumen-pokemon-live.service", "-p", "ActiveState,SubState"],
+            capture_output=True, text=True, timeout=10)
+        svc = dict(l.split("=", 1) for l in r.stdout.strip().splitlines() if "=" in l)
+    except Exception as e:
+        return False, f"systemctl show failed: {e}", True
+
+    active = svc.get("ActiveState", "?")
+    if active != "active":
+        return False, f"lumen-pokemon-live.service ActiveState={active}/{svc.get('SubState','?')}", True
+
+    try:
+        files = sorted(POKEMON_EVENTS.glob("live-agent-*.jsonl"),
+                        key=lambda f: f.stat().st_mtime, reverse=True)
+    except Exception as e:
+        return False, f"cannot list {POKEMON_EVENTS}: {e}", True
+    if not files:
+        return False, f"ActiveState=active but no live-agent event files under {POKEMON_EVENTS}", True
+    f = files[0]
+
+    last_turn_ts = None
+    try:
+        with open(f, errors="replace") as fh:
+            for line in fh:
+                try:
+                    e = json.loads(line)
+                except json.JSONDecodeError:
+                    continue
+                if e.get("kind") == "turn_summary":
+                    last_turn_ts = e.get("t")
+    except OSError as e:
+        return False, f"cannot read {f}: {e}", True
+
+    now = time.time()
+    if last_turn_ts is None:
+        file_age = now - f.stat().st_mtime
+        if file_age > POKEMON_START_GRACE_S:
+            return False, f"{f.name}: no turn_summary event {file_age/60:.0f}m since file start", True
+        return True, f"{f.name}: no turns yet, {file_age/60:.0f}m since start (within grace)", False
+
+    try:
+        turn_age = now - datetime.fromisoformat(last_turn_ts.replace("Z", "+00:00")).timestamp()
+    except Exception as e:
+        return False, f"bad turn_summary timestamp {last_turn_ts!r}: {e}", True
+
+    if turn_age > POKEMON_STALL_CRIT_S:
+        return False, (f"NO TURNS in {turn_age/60:.0f}m though ActiveState=active — "
+                        f"process alive but not progressing (wq#908 freeze shape)"), True
+    if turn_age > POKEMON_STALL_WARN_S:
+        return False, f"no turns in {turn_age/60:.0f}m (ActiveState=active)", False
+    return True, f"last turn {turn_age/60:.1f}m ago", False
+
 PROBES = [
     ("probe-output-rate",     probe_output_rate,     False),
@@ end of PROBES list @@
     ("probe-storage-integrity", probe_storage_integrity, False),
+    ("probe-pokemon-liveness", probe_pokemon_liveness, False),
 ]
```

## Diff — `/home/lumen/sentinel/config/config.yaml`

The gatus push token was pasted in literally when this report was written; wq#2116
redacted it. It is the shared `5-probes` token and it is in this repo's git history,
so it rotates with the rest of wq#1466's scope.

```diff
   - name: probe-storage-integrity
     group: 5-probes
     token: "***REDACTED-wq2116***"
     heartbeat: { interval: 50m }
     alerts: [{type: ntfy, send-on-resolved: true, description: "Fleet storage/mount integrity probe failed (missing mount / degraded array / config drift) — wq#421"}]
+  - name: probe-pokemon-liveness
+    group: 5-probes
+    token: "***REDACTED-wq2116***"
+    heartbeat: { interval: 50m }
+    alerts: [{type: ntfy, send-on-resolved: true, description: "lumen-pokemon-live ActiveState=active but no turn_summary progress (or service down) — wq#908, systemd active proved nothing during the 08-23 12h freeze"}]
```

## Files touched (outside this repo, not git-tracked — house convention for
`/home/lumen/sentinel`, matches existing `*.bak.<ts>_pre_wqNNN` backups already in that
directory)

- `/home/lumen/sentinel/probes/sentinel_probes.py` — added `probe_pokemon_liveness()`,
  registered in `PROBES`. No backup was taken before this edit (see note below); the
  change is purely additive (new function + one new list entry), verified by diff-review
  of the tool-call history in this session.
- `/home/lumen/sentinel/config/config.yaml` — added `probe-pokemon-liveness`
  external-endpoint entry. Backed up first: `config.yaml.bak.20260823_pre_wq908`.
