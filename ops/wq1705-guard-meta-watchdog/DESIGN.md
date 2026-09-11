# wq#1705 — guard-meta-watchdog: a dead firefighter must surface

## The gap

On 2026-09-10 seven guard/watchdog units were sitting in `failed`: pat-scan, the
drift guards, budget-alert and friends. Detection was never the problem.
`lumen-failed-units` had seen every one of them. Its own header says why they
stayed dead anyway:

> EDGE-TRIGGERED. Alerts on units NEWLY failed since last run ... A unit that
> stays failed does NOT re-alert every cycle.
>
> It NEVER restarts, resets, or mutates any unit. Reporting only. Correctors own
> remediation; this owns visibility.

Both halves of that are correct design. Constant re-alerting is what trains you
to ignore alerts, and mixing detection with remediation is how you get the
two-agents-restarting-lumen-agent race in REGISTRY.yaml. But the "correctors"
that were supposed to own remediation all check an **allowlist of named units**
(`smart-watchdog`, `self-healer`, `state-reconcile`), and no allowlist enumerated
these guards. So a guard died, produced exactly one notification, and then
nothing — no retry, no re-alert, no owner. The firefighter was dead and the smoke
alarm had already stopped ringing.

This ticket builds the missing corrector, and only the corrector.

## What it does

A 10-minute timer runs one sweep (`staged/guard_meta_watchdog.py`):

| unit state | action |
| --- | --- |
| crashed guard | `reset-failed` then `restart`, exponential backoff between tries |
| crashed guard, `MAX_ATTEMPTS` (3) restarts failed | **one** aq row + **one** urgent page, then stop trying |
| guard healed 3x inside 24h and failed again | **one** aq row + page — flapping, not healthy |
| guard that exited 1 *reporting a finding* | nothing. See below. |
| unit that is not a guard | nothing. `lumen-failed-units` owns its visibility. |
| tracked unit no longer failed | clear backoff state, low-priority CLEARED notice |

Escalation is `aq add guard-selfheal <unit> <title> <msg>`. `aq` dedups on
`(source, key)`, and the sweep additionally records `escalated_at` in its own
state, so a permanently stuck guard produces one row and one page — not one every
ten minutes. `source='guard-selfheal'` is deliberately distinct from
`source='failed-unit'`: there were 37 open `failed-unit` rows when this was
written, and a row that means "automatic remediation has given up" must not land
in that pile.

## Why a finding is not a crash

wq#1657 established the guard exit-code contract. Under Mode B a guard tagged
`X-Lumen-Guard=finding-exit` (or named in `guard-finding-exit.list`) exits **1**
to signal *it found something*, not that it broke. Restarting such a unit would
re-run the scan, re-find the same thing, fail again, and burn all three attempts
for nothing — while making the real finding look like a flapping service.

So the classifier reuses wq#1657's rule exactly: **exit 1 from a declared
finding-exit guard is a finding; any other nonzero exit, and any signal, timeout
or start-limit result, is a crash — tag or no tag.** Verified against the live
box on 2026-09-11:

- `lumen-pat-scan.service` — `Result=exit-code ExecMainStatus=1`, on the bridge
  list → **finding**, left alone.
- `repo-visibility-sentinel.service` — on the same bridge list, but
  `Result=exit-code ExecMainStatus=2` → **crash**, restarted. A listed guard can
  still genuinely die, and this one had.

wq#1657 is staged-but-not-deployed (`/home/lumen/bin/guard-finding-exit.list`
does not exist yet), so `staged/guard-finding-exit.list` ships as a fallback.
`FINDING_LIST_PATHS` prefers wq#1657's path, and `install.sh` refuses to clobber
it, so when that ticket lands there is exactly one list.

## Why role-detection, not an allowlist

An enumerated list of guards is the exact failure mode this ticket exists to fix
— it is what let `logrotate.service` rot for six days. A unit is treated as a
guard when its **name or its own `Description`** carries a guard-role token
(`guard`, `watch`, `sentinel`, `canary`, `drift`, `scan`, `alert`, `audit`,
`probe`, `lint`, `sweep`, `monitor`, `healer`, `reconcile`, `deadman`, `smoke`,
`check`, `verify`). `langfuse-evaluator-smoke.service` — described as a "weekly
connectivity check", named by no list anywhere — is picked up by exactly that
rule.

`PROTECTED` (default `lumen-agent.service`) is the counterweight: units that
match the role test but must never be auto-restarted, because something may be
mid-turn inside them.

## Safety properties

- **Fail-open.** Any internal error prints and exits 0 with state untouched. A
  meta-watchdog that crashes must not become the eighth dead guard.
- **Mode A compliance** (wq#1657): exits 0 whether or not it found anything;
  findings leave via `ntfy-send` + `aq`. Any nonzero exit from this unit is a
  real crash.
- **Bounded action.** It only ever `reset-failed` + `restart`s. It never stops,
  disables, masks, or edits a unit, and it only touches units systemd already
  reports as `failed`.
- **`--dry-run`** reads the live system and refuses every mutation, so "what
  would this do right now" never costs a restart. `install.sh` prints it rather
  than starting the service itself.
- **Atomic state** via `os.replace`, corruption-tolerant on read.

## Verify

```
python3 ops/wq1705-guard-meta-watchdog/test_guard_meta_watchdog.py   # 21 tests
python3 ops/wq1705-guard-meta-watchdog/staged/guard_meta_watchdog.py --dry-run
sudo ops/wq1705-guard-meta-watchdog/install.sh
```

## Tuning

All via environment (drop-in on the unit): `GUARD_META_MAX_ATTEMPTS` (3),
`GUARD_META_BACKOFF_BASE_S` (300), `GUARD_META_BACKOFF_CAP_S` (3600),
`GUARD_META_FLAP_LIMIT` (3), `GUARD_META_FLAP_WINDOW_S` (86400),
`GUARD_META_PROTECTED`, `GUARD_FINDING_LIST`, `GUARD_META_STATE`.
