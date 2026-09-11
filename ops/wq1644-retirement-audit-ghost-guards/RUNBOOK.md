# wq#1644 — retirement-audit check D: flag guards that name ghosts

Source: `foreman/research/FLEET_SERVICE_ATLAS.md` §6 (wq#1627 audit, captured
2026-09-10 06:35–06:50 UTC): *"Dead guards over dead systems"* — split-watch's
`UNITS` list points at agent5 units that no longer exist, the self-healer's
`stall:` rule still watches the masked `lumen-agent`, and the healer's
compiled-in `FALLBACK` table names three units retired on 2026-08-28. Nothing
on the fleet checks that a **guard's own config** still points at something
real — `retirement-audit` checks *retired things alive*, not *guards watching
retired things*. Prescribed fix (verbatim from the atlas row): add a
retirement-audit check D that resolves every unit/container named by
`healer_watch_table.yaml`, `split_watch.py` `UNITS`, `lumen_status_monitor.py`'s
svc list, `aq_escalate`'s `RUNBOOKS`, `drift-assert`'s `GOLDEN`, and
`lumen-failed-units`'s allowlist against live systemd/docker state.

## Design

Six source-specific extractors turn each guard's own config into a flat list
of `(kind, name[, node])` references, each fed through one shared prober:

| source | file | what's extracted |
|---|---|---|
| `healer_watch_table.yaml` | `/home/lumen/agent/healer_watch_table.yaml` | `units:` (kind per entry) + `stall:` keys (always systemd) |
| `split_watch.py UNITS` | `/home/lumen/agents/split-watch/split_watch.py` | the `UNITS = [...]` list (systemd) |
| `lumen_status_monitor.py svc list` | `/home/lumen/agent/lumen_status_monitor.py` | the `for s in [...]` list feeding `svc(s)` (systemd) |
| `aq_escalate RUNBOOKS` | `/home/lumen/bin/aq_escalate.py` | `systemctl <verb> <unit>` / `docker ... <name>` mentions inside each `RUNBOOKS` value string |
| `drift-assert GOLDEN` | `/home/lumen/bin/drift-assert` | same shell-ref extraction applied to each `GOLDEN` row's check command, scoped to `host == "lumen"` or a host already declared in `retired_services.yaml`'s `nodes:` map |
| `lumen-failed-units` | `/home/lumen/bin/lumen-failed-units` | wq#1657's (currently staged, not yet deployed) `FINDING_LIST=<path>` guard-exit allowlist, if wired |

Parsing is regex/`ast.literal_eval` only — **no source file is ever imported
or executed** by the audit, so a bug or side effect in one of these guard
scripts can't leak into retirement-audit's own process.

Each reference is then probed read-only, reusing check A's existing
`run_on()` (SSH/never_touch/reachability already solved there):

- **systemd**: `LoadState` in `(not-found, masked)` → **ALERT** ("ghost").
  Loaded-but-inactive is fine — plenty of guard targets are oneshots/timers
  that legitimately sit inactive between fires (the whole point of the
  `stall:` rule watching `lumen-agent` is to self-arm if it's ever revived).
- **docker**: not present at all → **ALERT**; present but not running →
  **WARN** (a real container, just not up right now — not a ghost); running →
  OK.
- Node unreachable / docker not queryable → **SKIP** (no false certainty,
  matches check A/B's existing skip convention).
- Source file itself unreadable → **SKIP** with the path in `target`.

`lumen-failed-units`'s `guard-finding-exit.list` mechanism is still staged
(`ops/wq1657-guard-exit-convention/`, not yet applied to the live script) —
today it contributes zero references, by design (`extract_failed_units_allowlist`
returns `None` when `FINDING_LIST=` isn't found in the live text). Once wq#1657
ships, check D starts validating that list's entries for free with no further
code change; if the list is wired but the file itself is missing, that's a
**WARN** (the classifier fails open to "not a finding-exit guard", which is
safe but means the allowlist isn't doing anything).

## Sweep (live run, 2026-09-10 23:57 UTC, `--local-only --no-alert`)

24 references extracted across the 5 currently-wired sources (6th,
`lumen-failed-units`, contributed 0 — not deployed yet). **8 alerts**:

| source | ghost | LoadState | note |
|---|---|---|---|
| `healer_watch_table.yaml` (`stall:`) | `lumen-agent` | masked | known, deliberate per wq#1112 — self-arms if Frank ever revives it, but is exactly the "stale entry" the atlas flagged |
| `split_watch.py UNITS` | `lumen-planner` | not-found | agent5 retired |
| `split_watch.py UNITS` | `lumen-generator` | not-found | agent5 retired |
| `split_watch.py UNITS` | `lumen-evaluator` | not-found | agent5 retired |
| `lumen_status_monitor.py svc list` | `lumen-agent` | masked | same ghost as above, different guard |
| `lumen_status_monitor.py svc list` | `lumen-orchestrator` | not-found | **new since the wq#1627 atlas snapshot** — atlas (06:35–06:50 UTC) called this one "verified ACTIVE, not retired"; independently reconfirmed live (`systemctl show lumen-orchestrator -p LoadState` → `not-found`) — it was retired sometime in the last ~17h and the monitor's svc list was never updated. This is exactly the class of drift check D exists to catch, caught on its first real run. |
| `drift-assert GOLDEN` | `lumen-agent` (`lumen-agent-enabled` row) | masked | GOLDEN asserts `is-enabled` should be `enabled`; the unit is masked — GOLDEN itself is stale |
| `drift-assert GOLDEN` | `lumen-agent` (`lumen-agent-notify` row) | masked | same unit, second stale GOLDEN row |

1 **skip** (`vaultwarden` on `maat` — docker not queryable from this session,
same as check A's existing remote-docker skip behavior). The remaining 15
references (redis-server, lumen-messenger, cloudflared, lumen-memoryd,
mcp-gateway, desktop-commander-mcp, lumen-pokemon-live, ntfy, litellm ×2,
lumen-messenger (status monitor), lumen-litellm-auth-monitor, litellm
(aq_escalate docker mention), homeassistant@bes, gitea@anubis) all resolved OK.

No fix to the ghosts themselves in this ticket — wq#1644 is the *detector*.
The `lumen-agent`/`stall:` and `drift-assert` GOLDEN staleness were already
flagged (and left as deliberate/tracked) by the wq#1627 atlas and wq#1112; the
new find is `lumen-orchestrator` in `lumen_status_monitor.py`, which nobody
had caught yet — worth a follow-up ticket to either restore the unit or drop
it from the monitor's svc list.

## Test-to-failure

`selftest` (offline, pure functions only, no live systemctl/docker/SSH calls)
gained 21 new checks for check D:

1. `ghost_verdict_systemd` — not-found/masked → alert; loaded (active or
   inactive) → ok.
2. `docker_ghost_verdict` — running → ok; present-not-running → warn (not a
   ghost); absent → alert.
3. `extract_healer_units` — `units:` entries keep their declared `kind`,
   `stall:` keys surface as systemd, `ports:` does not double-count.
4. `extract_bracket_list` — both call shapes (`UNITS = [...]` and
   `for s in [...]`), plus a no-match case.
5. `extract_shell_refs` — each of the 5 patterns (systemctl verb, docker
   inspect -f, docker restart/stop/start, `docker ps | grep -c`, prose
   "docker X recreation"), plus a non-match (psql) proving it doesn't
   over-match.
6. `extract_aq_runbooks` — a synthetic `RUNBOOKS` dict yields both the
   systemd and docker ref from one value string.
7. `extract_drift_golden` — a synthetic `GOLDEN` keeps a `lumen` row and a
   roster-declared (`maat`) row, drops a row for a host not in the roster.
8. `extract_failed_units_allowlist` — `FINDING_LIST` absent → `None`; present
   but file missing → the `("__missing__", path)` sentinel.

All existing check A/B/C selftest assertions are unchanged and still pass.

Run: `python3 ops/wq1644-retirement-audit-ghost-guards/staged/retirement-audit
selftest` → `ALL PASS` (38 checks total, up from 17).

## Apply

`/home/lumen/bin/retirement-audit` lives outside git (own repo, `lumen-bin`;
this branch is `foreman` and cannot commit to it directly) and is a **primary
tree — workers can't write there directly (wq#1719/wq#900 guard)**. Following
the wq#1657 convention: the finished file is staged here
(`staged/retirement-audit`), with `retirement-audit.diff` as the reviewable
change against the live original (`/home/lumen/bin/retirement-audit`,
unchanged as of this ticket — verified via `diff`, `py_compile`, and a live
`selftest`/`run --local-only` pass against the *staged* copy in this ops
folder). Deploying is: `cp staged/retirement-audit /home/lumen/bin/` (after a
timestamped backup) from a lane with write access to `/home/lumen/bin`, then
`retirement-audit selftest` + `retirement-audit run --local-only` once more
against the live path to confirm.

## Rollback

Not applicable yet — nothing has been applied to the live tree. Once deployed,
rollback is restoring the pre-deploy backup the deploy step takes.
