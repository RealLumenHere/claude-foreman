# wq#3349 sentinel-watch

`/home/lumen/bin/sentinel-watch`, run 4x daily by `lumen-sentinel-audit.timer` (01/07/13/19:17 UTC).
Read-only sweep; each finding becomes one wq ticket (`[sentinel] <class>/<subject>:`), deduped against every
non-closed ticket and carrying `--liveness 'sentinel-watch --check <key>'` so it closes when the condition clears.
Frank is paged (ntfy lumen-alerts) only for P0/P1 findings that are human-only (key mint, wq/sentinel blind)
or that survived 2 (P0) / 4 (P1) sweeps, meaning agents did not fix them. 24h page cooldown per key.

Classes: guard (named timers installed, active, armed; paired service not failed), probe (firing/stale),
aq (readable; noisy sources grouped), svc (failed, crash-looping), disk (lumen + ssh-reachable nodes),
litellm (container, completion probe, rungs cooled by fleet-selfheal), wq (queue readable).

Name note: `lumen-sentinel-watch.timer` already exists (Gatus reciprocal watchdog, every 5 min). Unrelated.
`overlay-drift-guard` was never built (wq#3350); listed as known-absent, not ticketed.
Overlap: fleet-selfheal (wq#3354) acts live on guards/crash-loops/disk/providers/nodes; this is the independent
4x-daily backstop and the only thing that watches aq, probes and failed units.

Flags: `--dry-run` touches nothing; `--check KEY` exit 0 clear / 1 present; `--only a,b`; `--json`.
Deploy: `bash install.sh` ; verify: `bash install.sh --verify`. Rollback line printed by install.sh.
Log: /home/lumen/logs/sentinel-watch.log (Pacific). State: /home/lumen/agents/sentinel-watch/state.json.
