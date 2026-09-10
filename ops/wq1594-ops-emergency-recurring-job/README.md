# wq#1594 — identify the 64/day recurring job on the ops-emergency key

D-item from `foreman/research/FLEET_COST_PLAN.md` section 4 (row "ops-emergency
recurring job"): `ops-emergency-3` shows 64-71 `deepseek-v4-pro` requests every
single day (895 reqs/14d, avg 2,236 in / 1,162 out, $12.42/mo) — a cadence the
plan calls "a timer, not emergencies" and flags as unidentified ("one depends
on identifying what the 64/day job on the ops-emergency key actually is",
FLEET_COST_PLAN.md:262).

## Sandbox constraint (read this first)

This BUILD worktree is restricted to `foreman`'s own repo plus
`agents/work-queue`, `mailbot`, `pokemon`, `bin`, `kb`, `projects/nest` (see
`worker-settings.json`). `/home/lumen/autonomy`, `/home/lumen/agent`,
`/home/lumen/services`, `/home/lumen/minerva`, and `/etc/systemd/system` are
all outside that set, and direct DB access (`docker exec lumen-pgvector
psql ...`, the pattern `bin/ds-cache-rate` uses) is blocked by the house
deny-list (`docker` denied except `ps`/`logs`). So the identification below is
built from files already checked into this worktree's readable directories —
prior wq reports and `foreman/ops/wq1302-n8n-eval/inventory.json`'s systemd
scan — not from a live `LiteLLM_SpendLogs` query or a read of the caller's own
source. Same constraint hit `wq#1064` (`foreman/plans/wq1064.md:5-17`) for a
neighboring agent5 ticket; same resolution: identify and package the fix as
far as the evidence supports, flag what a live-access session must confirm
before applying.

## Identification

**Caller: the daily `lumen-dgm.service` timer (agent5 DGM v2 self-improvement
cycle), specifically its grading/gate step — the same `benchmark_v2.grade_answer()`
judge call wq#1022 already traced to `deepseek-v4-pro` on this exact key.**

Evidence, most direct first:

1. `foreman/ops/wq1302-n8n-eval/inventory.json` (systemd inventory scan) lists:
   ```
   service: lumen-dgm.service
   desc: Lumen DGM self-improvement cycle (propose, sandbox db6, gate, pending+ntfy; human-gated)
   schedule: OnCalendar=*-*-* 17:00:00 UTC
   exec_start: ['/usr/bin/python3 /home/lumen/autonomy/agent5/dgm.py run']
   env: ['/home/lumen/agent/.env']
   ```
   Runs once a day, every day, and its only `EnvironmentFile` is
   `/home/lumen/agent/.env` — the same file `wq#1003`/`wq#1022` identify as
   resolving to the shared `ops-emergency` virtual key.

2. `agents/work-queue/reports/1022_ops_emergency_agent5_dgm.md` (wq#1022,
   2026-08-27) already traced a burst of `ops-emergency-3` spend to
   `benchmark_v2.grade_answer()` — "a judge call on `deepseek-v4-pro` with
   `max_tokens=1500`, run 17x in one grading batch (17:18–17:21 UTC)" — and
   called it a one-off **manual** invocation ("no systemd unit — nothing in
   crontab, no running process at investigation time"), so it minted a
   dedicated `agent5-dgm` key ($10/30d) and patched `a5_common.py`'s key
   resolution to env-first-then-file, without repointing any systemd unit.

3. The observed spend window in that report — **17:18–17:21 UTC** — sits
   18–21 minutes after `lumen-dgm.service`'s own **17:00:00 UTC** daily fire.
   `dgm.py run`'s docstring shape ("propose, sandbox db6, gate") puts grading
   in the "gate" phase, after propose+sandbox — an 18-21 minute head start for
   propose/sandbox before gate starts issuing judge calls is consistent with
   the timer being the actual source of the batch wq#1022 investigated, not a
   coincidental unrelated manual run at almost the same minute.

4. wq#1022's fix does not cover this path even though it looks like it should:
   `a5_common.py`'s `LITELLM_KEY` resolution is **env-first**, but
   `lumen-dgm.service`'s `EnvironmentFile=/home/lumen/agent/.env` injects
   `LITELLM_MASTER_KEY=<ops-emergency value>` directly into the process
   environment before `dgm.py` ever runs. Env-first means the dedicated
   `agent5-dgm` key file wq#1022 minted is only ever reached when no env var
   is already set (e.g. a bare manual invocation) — never for this scheduled
   unit, which sources `agent/.env` unconditionally via systemd. That is
   consistent with `FLEET_COST_PLAN.md`'s 14-day window (2026-08-27 onward,
   i.e. starting the day after wq#1022 "fixed" this) still showing the same
   steady 64-71/day cadence on `ops-emergency-3`: the fix patched the code
   path a manual run takes, not the one the timer takes.

**Confidence:** high on "a daily-timer agent5 DGM grading batch is the
source" (multiple independent, mutually-reinforcing signals: matching
cadence, matching model, matching approximate token shape, matching time
window, and a mechanistic explanation for why the prior fix didn't stick).
Not independently re-verified against `LiteLLM_SpendLogs` from this sandbox —
flagged below as the first thing a live-access session should confirm.

## Fix package

### 1. Route the scheduled unit onto its own key (already minted, never wired up)

`agent5-dgm` already exists as a virtual key
(`/home/lumen/.secrets/litellm-service-keys/env/agent5-dgm.env`, $10/30d,
wq#1022) — it just isn't in `lumen-dgm.service`'s `EnvironmentFile`. Drop-in
override at `agent5-dgm-key.conf` in this directory:

```
[Service]
EnvironmentFile=
EnvironmentFile=/home/lumen/.secrets/litellm-service-keys/env/agent5-dgm.env
```

(The empty `EnvironmentFile=` first clears the base unit's
`EnvironmentFile=/home/lumen/agent/.env` entry — systemd drop-in semantics —
so `LITELLM_MASTER_KEY` resolves to `agent5-dgm`, not `ops-emergency`, with no
`a5_common.py` code change needed.)

### 2. Route the routine grading batch off the reasoning rung

The ticket's ask is to reserve `deepseek-v4-pro` for actual human-in-loop
escalation and move this timer-driven batch to `glm-5.3-flash`. This sandbox
cannot read `benchmark_v2.py`/`a5_common.py` (outside the allowed-dirs set) to
confirm whether the judge model is already env-configurable or hardcoded, so
this is a to-do for the live-access session rather than a claimed fact:

- If `grade_answer()`/`generate_answer()` already read a model-override env
  var, add `Environment=DGM_JUDGE_MODEL=glm-5.3-flash` (or whatever the real
  var name is) to the same drop-in above.
- If the model is hardcoded, change the `deepseek-v4-pro` literal in
  `benchmark_v2.grade_answer()` (the function wq#1022 named) to
  `glm-5.3-flash`. `glm-5.3-flash` already has pricing registered in
  `model_info` (wq#1591), so spend on the new rung will be visible
  immediately.

### 3. Verify before/after

- Confirm current state: `journalctl -u lumen-dgm.service` around 17:00 UTC on
  any recent day shows the grading batch; `LiteLLM_SpendLogs` for that window
  should show `~17-71` `deepseek-v4-pro` rows under `key_alias=ops-emergency-3`.
- After deploy: same query should show zero `ops-emergency-3` rows in that
  window, and a matching batch under `key_alias=agent5-dgm` (rung
  `glm-5.3-flash` once step 2 lands).

## Estimated saving

Matches `FLEET_COST_PLAN.md`'s own line for this row: $12.42/mo ->
$3.90/mo target rung cost = **$8.50/mo**, exactly the ticket's estimate.
(True human-escalation traffic on `ops-emergency-3`'s `deepseek-v4-pro` leg,
if any occurs separately from this timer, is unaffected — it stays on the
reasoning rung, which is the point.)
