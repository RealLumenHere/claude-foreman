# OPS-AUDIT A10: Lane Autonomy Defaults — wq#760

**Author:** claude-web-755-760  
**Date:** 2026-08-23  
**Campaign:** FLEET_OPS_AUDIT_2026-08-21 (F2)

## 1. Problem Statement

Lanes get created by `wq-lane-init` but are not registered with the foreman
dispatcher config. A config default silently disables the thing it configures.
The build lane was ABSENT from dispatcher config entirely until the ops-audit
campaign added it — 27 open build tickets were never dispatcher-visible.

## 2. Current State (measured 2026-08-23 05:42 UTC)

### 2a. Dispatcher config.json lanes (9 entries)

| Lane | enabled | Notes |
|---|---|---|
| foreman-tok | true | Active |
| lpp | true | **0 open tickets** (lane exists in config, empty in DB) |
| questfree | true | **0 open tickets** |
| pstack | true | **0 open tickets** |
| fleet-fires | true | requires_approval was removed (git: 356a273) |
| foreman-auto | true | Active |
| build | true | Added by ops-audit campaign |
| mission-control | true | Active |
| user | false | Correctly disabled (Frank-only, wq claim refuses) |

### 2b. Lanes in queue.db with open tickets NOT in dispatcher config

| Lane | Open tickets | In dispatcher? | Impact |
|---|---|---|---|
| fleet | 7 | **NO** | Never dispatched |
| fleet-spend | 1 | **NO** | Never dispatched |
| memory | 3 | **NO** | Never dispatched |
| ops | 1 | **NO** | Never dispatched |
| pokemon | 8 | **NO** | Never dispatched |
| quest-repair-research | 1 | **NO** | Never dispatched |
| research | 3 | **NO** | Never dispatched |

**24 open tickets across 7 lanes are invisible to the dispatcher.**

### 2c. Config entries with 0 open tickets (phantom lanes)

| Lane | In dispatcher? | Open tickets | Assessment |
|---|---|---|---|
| lpp | yes | 0 | Config entry exists, nothing to dispatch |
| questfree | yes | 0 | Config entry exists, nothing to dispatch |
| pstack | yes | 0 | Config entry exists, nothing to dispatch |

These were recently enabled (git: 7e837af, 356a273) but have no work. Not harmful,
but they show the other side of the drift: lanes added to config without tickets.

## 3. Root Cause Analysis

`wq-lane-init` creates:
1. A `<LANE>_DISPATCH.md` from template
2. A board file in `boards/`
3. Runs `dispatch-lint --universal` on the new file

It does **NOT**:
- Add the lane to `foreman/dispatcher/config.json`
- Validate that the lane exists in config
- Print a warning that the lane won't be dispatched

The dispatcher's `foreman_dispatcher.py` (line 82) only scans "open tickets in
config-enabled lanes." Any lane not in config is invisible — and since `wq add`
accepts any lane name, tickets accumulate silently.

**There is no probe or lint that detects the drift.** `dispatch-lint` checks
dispatch FILES against RETIRED_RULES and UNIVERSAL_RULES — it does not check
config vs queue alignment.

## 4. Recommendations

### 4a. Registration at lane-init (wq-lane-init patch)

When `wq-lane-init` creates a lane, it should:
1. Add an entry to `config.json` with `enabled: false` and a comment
2. Print: "Lane '$lane' added to dispatcher config as DISABLED. Enable with:
   `jq '.lanes.$lane.enabled = true' config.json | sponge config.json`"
3. Emit a warning if `--enable` is passed and the lane has `requires_approval`
   implications

This makes the choice EXPLICIT at creation time. The operator sees
"enabled: false" and decides, rather than the lane silently not existing.

### 4b. Drift probe (dispatch-config-lint)

A new probe that should run on every foreman-health pass:

```
FAIL if: lane exists in queue.db with status=open AND
         lane NOT in dispatcher config.json
WARN if: lane exists in config.json AND
         lane has 0 tickets in any status
```

Wire into `lumen-foreman-health.timer` (already runs every 5 min). FAIL → ntfy
alert on lumen-fleet. This catches both directions of drift.

### 4c. Per-lane enable/disable recommendations

| Lane | Open | Recommendation | Reason |
|---|---|---|---|
| fleet | 7 | **Enable** | Fleet infra work; same model/archetype as build |
| fleet-spend | 1 | **Enable** (merge into fleet) | Single-purpose lane, fold into fleet |
| memory | 3 | **Enable** | Minerva memory work; same worker class |
| pokemon | 8 | **Enable** | LPP tickets; has its own dispatch file |
| research | 3 | **Keep disabled** | Article-research uses Frank-pasted dispatch, not foreman |
| quest-repair-research | 1 | **Keep disabled** | Hardware RE, needs human judgment |
| ops | 1 | **Merge into build** | Single ticket, same archetype |
| lpp | 0 | **Keep** | Config entry ready; tickets will appear |
| questfree | 0 | **Keep** | Config entry ready |
| pstack | 0 | **Keep** | Config entry ready |
| fleet-fires | - | **Re-gate** (see below) | requires_approval was removed |

### 4d. fleet-fires requires_approval

Git history (356a273) shows `requires_approval` was set to `false` in the
"full autonomy" commit. The ticket says fleet-fires keeps requires_approval
regardless — it is force-set in code. Verified: `grep -n 'fire_approval'`
in foreman_dispatcher.py shows the dispatcher checks for an approval FILE
(`approvals/wq<tid>.approved`) for fleet-fires lane tickets regardless of
the config flag. The config `requires_approval: false` is misleading but
not operationally wrong — the code overrides it. Recommend: set it back to
`true` in config for documentation accuracy.

## 5. P0/P1 Findings

- **P1: 7 lanes with 24 open tickets invisible to dispatcher.** wq-lane-init
  does not register lanes. No probe detects the drift. → **Seed ticket:
  wq-lane-init config registration + drift probe.**
- **P1: fleet-fires config says requires_approval=false but code enforces it.**
  Misleading config. → **Seed ticket: reconcile config to match code.**
- **P0: No automated way to detect lane/config drift.** The build lane was
  missing for the entire build campaign until manual intervention. Same class
  of bug can recur for any new lane. → **Seed ticket: dispatch-config-lint
  probe wired to foreman-health timer.**
