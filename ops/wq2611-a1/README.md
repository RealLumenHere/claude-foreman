# wq#2611 — Degraded-state alerting: Gatus /pool probe -> aq, nephthys-awareness targets

Design: `MCP_CONTROL_PLANE_HA_DESIGN.md` §3.4 (Alerting, edge-triggered,
independent sink), specifically its 2nd and 3rd bullets:

> Gatus (sentinel) endpoint on `mcpw.lumennode.uk/pool` → aq row
> `mcp-pool-degraded` while `healthy_count<3`.
> nephthys-awareness already probes lumen dc-gateway; extend its target list
> with anubis/bes :3100/health.

(The 1st bullet — the Worker cron's own ntfy.sh/6h-repeat/"LAST NODE"
alerting — is §3.1 Worker code, not deployed; out of scope here, blocked on
the CF API token physical task per design §6, same blocker `wq2609`/`wq2613`
already hit for the rest of the HA rollout.)

## What's in this directory

| file | maps to |
|---|---|
| `sentinel_probes.diff` | reviewable record of the edit already applied live to `/home/lumen/sentinel/probes/sentinel_probes.py`: new `probe_mcp_pool_degraded()` (GET `/pool.txt` with the `X-Pool-Shared-Secret` header, FAIL+critical=True when `healthy_count < 3`) + its `PROBES` registration. Same outside-git-but-local-to-Lumen convention as `ops/wq897-litellm-auth-monitor-probe/` — **the change is already live**, this diff is not something to `git apply`. |
| `gatus_config.diff` | same for `/home/lumen/sentinel/config/config.yaml`: registers `probe-mcp-pool-degraded` as a Tier-2 external endpoint (group `5-probes`, 50m heartbeat, matching `probe-litellm-auth-monitor`/`probe-maat-vault`) so it shows on the Gatus dashboard and gets its own dead-man heartbeat. |
| `verify_mcp_pool_probe.py` | synthetic-degrade verification, run against the real `sentinel_probes.py` code (not a reimplementation): local HTTP stand-in for `/pool.txt`, scratch `AQ_DB`, intercepted `NTFY`/Gatus-push network sinks. Proves one degrade → exactly 1 open aq row + 1 urgent ntfy; a second FAIL while still degraded → same row bumped (count=2), zero additional ntfy (page-suppressed, not per-minute spam); recovery → row resolved + exactly 1 recovered ntfy. |
| `nephthys-awareness-targets.md` | apply instructions (not a diff — no local copy of the remote script exists) for the `anubis:3100/health` + `bes:3100/health` target-list addition to `bes-awareness.service` (nephthys-awareness's current home per wq#589). Blocked: this lane has no `fleet-exec` grant to Bes (`needs` column empty, same class as `kb/ISSUE-LOG.md` #740/#754). |

## Why the Gatus/probe half shipped and the nephthys-awareness half didn't

`/home/lumen/sentinel/{config/config.yaml,probes/sentinel_probes.py}` are
outside-git files **local to Lumen itself** — this BUILD lane can reach them
directly (same as wq#897's precedent), no ssh/fleet-exec needed.
`bes-awareness.service` lives on a different node this lane has no exec
grant for (confirmed live: `fleet-exec grant` → `DENY
[grant:grant_missing_env]`). Logged at `kb/ISSUE-LOG.md` #1064.

## Not yet live end-to-end (accepted, documented in the probe's own docstring)

`mcpw.lumennode.uk/pool.txt` and its shared secret don't exist yet — the
Worker rewrite (§3.1) is design-only per the doc's own header ("status:
DESIGN, not deployed"), blocked on the CF API token physical task (§6). Until
`/home/lumen/.secrets/mcpw-pool-shared-secret.env` is provisioned,
`probe_mcp_pool_degraded()` returns a non-paging PASS with an explicit "not
provisioned yet" detail (same shape as the `_stale_or_none()` guard other
probes use for a retired target, wq#1092) — it cannot false-page against a
route that isn't supposed to exist yet, and starts paging for real the moment
the secret file is dropped in place, no further code change needed.

## Verified this session

```
$ python3 ops/wq2611-a1/verify_mcp_pool_probe.py
[cycle1 degrade] ok=False critical=True detail='mcp-pool-degraded: healthy_count=1 < 3' open_rows=1 row_count=1 urgent_sent=1
[cycle2 still-degraded] ok=False critical=True open_rows=1 row_count=2 urgent_sent=1
[cycle3 recover] ok=True critical=False detail='healthy_count=3/3+ required' open_rows=0 recovered_sent=1
PASS: one degrade -> exactly one aq row + one urgent ntfy; repeat degrade -> same row bumped, no re-page; recovery -> row resolved + exactly one recovered ntfy.
```

## What this ticket actually closes today

- `probe_mcp_pool_degraded()` live in `/home/lumen/sentinel/probes/sentinel_probes.py`, registered in `PROBES`, wired into the existing edge-triggered aq/ntfy machinery (`main()`'s FAIL→`aq.ingest`/critical→`ntfy_urgent` with `REPAGE_S` suppression, PASS→`_edge_clear`/`ntfy_recovered`) — no new alerting code needed, this reuses the mechanism `probe-litellm-auth-monitor`/`probe-maat-vault` already prove out.
- `probe-mcp-pool-degraded` registered in `/home/lumen/sentinel/config/config.yaml`'s `5-probes` group for Gatus dashboard visibility + its own dead-man heartbeat.
- Synthetic-degrade proof against the real probe code, not a mock reimplementation (`verify_mcp_pool_probe.py`, output above).
- `kb/ISSUE-LOG.md` #1064: the nephthys-awareness half's remote-access blocker, plus staged apply instructions for the next session with Bes access.
