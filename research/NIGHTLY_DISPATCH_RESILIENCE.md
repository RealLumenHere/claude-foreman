# Nightly dispatch resilience — root cause and design

Status: DESIGN, review before code. Route: fable-5.1 research. Tree: claude-foreman @ f08b737, 2026-09-03.
Scope: the dispatcher/harness loop stalling every night for 7+ days (kb/ISSUE-LOG.md "2026-09-02
ORCHESTRATOR OPERABILITY FRICTION" items 3-4, wq#1330 wq#1368 wq#1369 wq#1372 wq#1401).

Every claim below was checked against the live tree or live state on 2026-09-03. Line numbers are
from the commits named; re-verify after landing anything.

## 0. The one-paragraph answer

The fleet already has a stall watchdog and it already fires. `dispatch_health.py` paged
"DISPATCHER STALLED" 145 times between 08-23 and 09-03 14:40 (alerts.db row aq#794, status=open).
Nothing consumes that page: `bin/aq_escalate.py:11` only escalates `source='probe'` rows, so the
dispatcher page never becomes a fleet-fires ticket and never triggers recovery. The auto-reprove
shipped for stale stamps (wq#1372) has succeeded zero times from the timer because the unit's PATH
does not contain `claude` (`harness_preflight.py:247` spawns the bare name). The stamp itself is
invalidated by hook edits and rule reordering, not just permission changes
(`harness_preflight.py:155`). And the per-ticket loop still has unwrapped calls that turn one bad
input into a dead pass (`foreman_dispatcher.py:891` above all). None of this is transient. All of
it is one class: safety machinery whose failure path is "skip, log, keep ticking" with no actor
behind the alert.

## 1. Full-class root cause

### 1.1 Timeline of the week (dispatcher.log, replayed with /tmp/passes.py)

| window (UTC) | passes | shape | skip reasons |
|---|---|---|---|
| 09-02 15:55 -> 09-03 02:06 | ~40 | pass_end spawned=0, candidates 43-59 | lane_unmapped 37-38, no_fresh_stamp 2-12 |
| 09-03 03:37 -> 05:34 | 31 | ERROR stage=pass ManifestError, no pass_end, exit 1 | (crash before spawn) |
| 09-03 06:20 -> 15:22 | 28 | pass_end spawned=0, candidates 46-50 | lane_unmapped 33, no_fresh_stamp 6-9, reprove_failed 2 hourly |
| 09-03 15:25 -> now | | healthy after manual `harness-preflight` x3 at 15:24 | |

Both stall shapes are in the log for ~20 hours of the last 24. The third shape (fleet up, nobody
told) is what Frank saw.

### 1.2 Shared mode A: the alert exists, nothing acts on it

- `dispatcher/dispatch_health.py:107-178` classifies a pass unhealthy on `error` without
  `pass_end`, or `pass_end spawned=0 candidates>0` without a no_slots halt. Replayed against the
  log cut at 09-02 22:00, 09-03 01:00 and 09-03 04:20: all three `WOULD ALERT`. The classifier is
  correct.
- `dispatch_health.py:52` dedups at 3600s; `lumen-foreman-health.timer` active since 08-31 12:15.
  ntfy cache (12h retention) shows nine urgent pages 09-03 06:28..14:40, one per hour.
- `agents/alert-queue/alerts.db` row 794: title DISPATCHER STALLED, source ntfy:lumen-alerts,
  first_ts 2026-08-23, last_ts 2026-09-03T14:40, count 145, status open.
- `bin/aq_escalate.py:5,11`: escalates only `source='probe' AND priority='high' AND count>=3`.
  Row 794 has source ntfy:lumen-alerts, priority 5. It is filtered out by design.
- lumen-alerts carried 159 messages in the cache window; 72 of them priority-5 repeats of two
  sentinel probes at 30-min cadence. A ninth hourly DISPATCHER STALLED at the same priority is
  indistinguishable from the floor.

Path that fails closed and silent: watchdog -> ntfy -> aq row (open, count++) -> nobody. There is
no code path from "dispatcher unhealthy" to any recovery action.

### 1.3 Shared mode B: auto-reprove cannot work from the timer

- `harness/harness_preflight.py:247` `run_live_proof(..., claude_bin="claude")`; `:253` execs it.
- `dispatcher/systemd/lumen-foreman-dispatch.service` `Environment=PATH=/home/lumen/bin:/usr/local/bin:/usr/bin:/bin`.
- `claude` is `/home/lumen/.npm-global/bin/claude`. Under the unit PATH `which claude` is empty.
- `harness/fworker.py:2566` already defaults `--claude-bin` to the absolute path. preflight did not.
- Effect: `dispatcher.log` `auto_reproved` count = 0, ever. Every reprove logs
  `reprove_failed detail="live proof errored: FileNotFoundError(2, ...)"` then the 1h cooldown
  (`foreman_dispatcher.py:94`, `:606-615`) parks every ticket for that repo. The hourly
  "preflight reprove failed" ntfy went to lumen-fleet (default topic), 14 times, next to 162
  LAUNCH_FAILURE messages.
- Manual `harness-preflight` from a login shell (PATH has .npm-global) works, which is exactly why
  it looked like "re-run by hand fixes it".
- Same class, second instance: `harness/fworker.py:532` calls ntfy-send with `--title`/`--priority`;
  `bin/ntfy-send:12-16` parses `-T`/`-p` only and treats unknown flags as message text. 162
  worker alerts in 48h delivered at default priority with the title in the body.

Class: a binary or flag resolved correctly in an interactive shell and incorrectly under systemd.
Where else it exists: any `subprocess.run(["claude", ...])` or bare-name exec reached from a unit.
`grep -rn '"claude"' harness dispatcher orchestrator` should be empty after the fix.

### 1.4 Shared mode C: the stamp stales on changes that do not alter what it proves

- `harness_preflight.py:155-157` `preflight_hash(rendered_settings, cmds)` hashes the whole
  merged settings dict. `permission_profiles.py:84` deep-copies `worker-settings.json` including
  `hooks` (4 entries) and passes them through. So a hook edit stales every migrated repo.
- `permission_profiles.py:68-82` builds `allow`/`deny` as ordered lists. wq#1382 (commit e954584)
  moved the base-allow union before the loop: identical rule set, different order, new hash,
  all three stamps stale.
- `harness_preflight.py:67-72` `FLEET_EXEC_PREFLIGHT_PROBES` are code constants appended to
  `cmds`; editing them stales the stamp without any permission change.
- 15 commits in 7 days touched the four hashed inputs (git log -- harness/worker-settings.json
  harness/permission-profiles harness/harness_preflight.py harness/permission_profiles.py); 46
  touched harness/ overall. The gate was shipped 09-02 (eb32276) so this is a 2-day-old check
  that has been stale more often than fresh.

What preflight actually proves: that the rendered allow/deny set does not deny any preflight
command, statically and live. The gating inputs are therefore exactly:
`sorted(set(allow))`, `sorted(set(deny))`, `permissions.defaultMode`, `sorted(set(cmds))`.
Hooks, key order, docstrings, model, and the rest of worker-settings.json are not.

### 1.5 Shared mode D: unwrapped per-ticket calls (the wq#1368 class)

`_do_pass` (`foreman_dispatcher.py:698-915`) wraps: `router.wq_show` (SystemExit, :755),
`unmet_deps` (SystemExit, :404), `candidate_model` (SystemExit + ManifestError, :429-435),
`concurrency.list_running_units` (LumenRunError -> break, :830), `spawner.dispatch_one` (returns
None, spawner.py:96-111). Everything else propagates to `run_pass`'s pass-level except at :965,
which logs, ntfys, and returns 1 with every remaining candidate abandoned. Unwrapped, per ticket:

| site | call | can raise | trigger |
|---|---|---|---|
| :891 | `harness_preflight.dispatch_gate_check(repo)` | FileNotFoundError, JSONDecodeError, OSError | `_load_json` on repos.d/<repo>.json, base.json, any profile fragment (`permission_profiles.py:35-37,67,73`); unwritable render dir (`:120-126`). A JSON typo in any profile file kills every pass for every migrated repo, every 20 min, until a human edits it. |
| :755 via router.py:731-732 | `router.wq()` | none, but **no timeout** | a wq/sqlite lock hangs the pass until TimeoutStartSec=600 SIGTERMs it: no pass_end, no error line, exit 1 |
| :780 | `router.repo_for_ticket(t)` | whatever needs.parse / repo_sweep.managed_repos raise | malformed needs tag, unreadable repo universe |
| :839 via router.py:405-407 | `router.assign` non-Manifest paths | FileNotFoundError, yaml.YAMLError | models.yaml missing or unparseable (only ManifestError is caught) |
| :862 | `PACING_HOOK` | ValueError/KeyError | malformed governor limits |
| :912 | `save_state` | OSError | disk full |

Per pass, also unwrapped: `runaway.stuck_ticket_ids` :704, `psi_pressure.check` :708,
`pacing.load_config` :719. And `circuit_breaker.check_and_trip` at :930 runs **outside** the
try/except at :960-968, so a breaker exception produces a Python traceback, exit 1, and no line
in dispatcher.log at all (invisible even to the classifier).

### 1.6 Ready>0 is inflated

33-38 of every pass's 43-59 candidates are `lane_unmapped` (the `fleet` lane has no LANE_REPO
entry, router.py:800). `candidates` in `pass_end` counts them. Any watchdog keyed on
`candidates>0` cannot tell a stall from a queue of structurally undispatchable tickets, and any
"ready" number shown to Frank overstates the backlog by ~3x.

### 1.7 Meta-cause

Every mechanism above was built as a gate: refuse, log a skip, keep ticking. Gates are correct for
a wrong answer (do not spawn on an unproven profile) and wrong for a machine that changes its own
inputs 40+ times a week: a gate with no retry, no actor, and no distinction between "changed" and
"changed in a way that matters" converts every commit into a stall. The fleet is not eating its
tail; it is starving in front of a locked door with the key in a queue nobody reads.

## 2. Resilience to self-modification

### 2.1 Stamp gating inputs (replace `preflight_hash`)

```
def profile_digest(rendered, cmds):
    perms = rendered.get("permissions", {})
    blob = json.dumps({
        "allow": sorted(set(perms.get("allow", []))),
        "deny":  sorted(set(perms.get("deny", []))),
        "mode":  perms.get("defaultMode"),
        "cmds":  sorted(set(cmds)),
    }, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()
```

- Stamp records `profile_hash` (above) and, separately, `settings_hash` (today's full-dict hash).
- `dispatch_gate_check` compares `profile_hash` only. A `settings_hash` mismatch with a matching
  `profile_hash` is emitted as `info stage=preflight reason=stamp_soft_stale` and dispatch
  proceeds. This is the "hooks changed / order changed / probe list reworded" case.
- Two stamps are equivalent iff their rule sets and command sets are equal. Adding a rule to
  allow IS a profile change and must reprove (it can create a new deny/allow interaction).
  Removing one is too. This does not loosen verification; it stops proving the same set twice.
- `FLEET_EXEC_PREFLIGHT_PROBES` and `INSPECTION_PIPE_PROBE` stay in `cmds` (they are commands
  the proof must cover), but a change to them is a real re-prove trigger and that is fine: it is
  one commit a month, not forty.

### 2.2 Reprove: retry transient, page persistent

`handle_stale_preflight` (`foreman_dispatcher.py:597-638`) becomes:

1. Fix the spawn: `harness_preflight.CLAUDE_BIN = os.environ.get("FOREMAN_CLAUDE_BIN",
   "/home/lumen/.npm-global/bin/claude")` and use it at `:253`. Also add
   `/home/lumen/.npm-global/bin` to the unit `Environment=PATH` as belt-and-braces. Both.
2. Classify the failure before deciding what to do:
   - `static lint failed` or `live proof found permission denials` -> **persistent**. The profile
     is genuinely broken. Page once (urgent, lumen-alerts), record `preflight_ntfy[repo]`, skip
     the repo this pass. Re-attempt after 1h as today.
   - `live proof errored` (OSError, TimeoutExpired, FileNotFoundError, non-zero claude exit,
     empty stream) -> **transient until proven otherwise**. Retry inside the same pass with
     backoff 0s / 30s / 120s (three attempts, bounded by TimeoutStartSec=600). Third failure ->
     page urgent with the literal exception, and set the cooldown. Never `skip` silently on the
     first error.
3. `preflight_ntfy` state gains `{repo: {ts, attempts, last_error}}` so the page says "3rd
   consecutive reprove failure, FileNotFoundError" instead of a one-liner.
4. A successful reprove after a page sends a `default`-priority "recovered" ntfy so the aq row
   can be auto-resolved (aq already keys Recovered: titles).

## 3. Fail open + loud

### 3.1 Quarantine, not crash: per-ticket exception boundary

Wrap the body of the per-ticket loop (`:735` to `:914`) in one boundary:

```
try:
    ... stages 3 .. 10 unchanged ...
except (KeyboardInterrupt, SystemExit): raise
except Exception as e:
    log.skip(tid, lane, "unhandled", "quarantined", detail=repr(e)[:300])
    quarantined.append((tid, repr(e)))
    continue
```

- Ticket-scoped exceptions cost that ticket a skip. `pass_end` gains `quarantined=N`.
- Repo-scoped failures (`dispatch_gate_check` raising on a broken profile file) would otherwise
  produce one quarantine per ticket of that repo. Cache the gate result per repo for the pass
  (`gate_cache[repo] = (ok, reason)`); on exception cache `(False, "profile_unreadable: ...")`
  and skip with reason `profile_unreadable`. One ntfy per repo per pass, deduped 1h.
- Halting conditions stay halts: kill switch, spawn cap, concurrency unreachable, no_slots.
  Those are correct pass-level decisions and the classifier already knows them.
- `circuit_breaker.check_and_trip` moves inside the try at `:960` so a breaker exception is a
  logged error, not a bare traceback. `router.wq()` gets `timeout=30`; a TimeoutExpired is
  handled as `vanished` for that ticket.
- Per-pass preamble calls (`runaway`, `psi_pressure`, `pacing.load_config`) each get a local
  try/except that degrades to their documented safe default (no stuck set, psi=ok, pacing
  default config) with an `error` event, so the pass still runs.

### 3.2 Dispatch-anyway vs fail-closed on a benign stale stamp

Fail-closed on a stale stamp was the right default when "stale" meant "the permissions changed".
After 2.1, "stale" means exactly that again, so the decision is only about the soft-stale case,
and there the argument is one-sided:

- The stamp proves the profile. A hook or ordering change does not change the profile. Refusing
  to spawn on it verifies nothing; it only converts a docs commit into an idle night.
- The residual risk of dispatch-anyway on soft-stale is a hook change that breaks the worker.
  That risk exists identically for a fresh stamp (hooks are not proved by preflight today either)
  and is caught by the circuit breaker within 8 attempts (`circuit_breaker.py`, threshold 0.75).
- Fail-closed on a hard-stale stamp stays. The fix is not to dispatch on an unproven profile; it
  is to reprove it in-pass with retries (2.2) so "hard-stale" lasts minutes, not until morning.

Decision: soft-stale -> dispatch + `stamp_soft_stale` info event. Hard-stale -> reprove now,
retry transient, page persistent, never silent.

### 3.3 The watchdog: make a silent stall impossible

Keep `dispatch_health.py` as the sensor (it is correct). Add the actor and the escalation:

1. **Sensor precision.** `pass_end` gains `dispatchable` = candidates minus structural skips
   (lane_unmapped, nodispatch_marker, frank_gated, deps_unmet, runaway_stuck, not_open,
   vanished). Classifier rule 2 becomes `spawned=0 AND dispatchable>0`. Also treat a pass with
   `pass_start` and no `pass_end` older than TimeoutStartSec as unhealthy (the hung-wq case).
2. **Actor.** New `dispatcher/dispatch_recover.py`, invoked by `dispatch_health.py` when the
   alarm condition holds for K=2 consecutive scored passes (not 3; the 20-min cadence makes 3 a
   one-hour blind spot). Bounded ladder, one rung per health tick, state in
   `runtime/recover_state.json`:
   - rung 1: `harness-preflight` every migrated repo (fixes the stamp class); log result.
   - rung 2: `systemctl restart lumen-foreman-dispatch.timer` and `systemctl start
     lumen-foreman-dispatch.service` (fixes a wedged unit / stale timer).
   - rung 3: `git -C /home/lumen/foreman status --porcelain` + `python3 -c "import json;
     json.load(open(...))"` over config.json and every permission-profiles/*.json; report which
     file is unparseable (fixes nothing, but the page names the file:line).
   - after rung 3 with no healthy pass: STOP recovering. File a fleet-fires ticket via aq_escalate
     (below) and page urgent. Never loop rungs; never touch DISPATCH_DISARM.
   - any healthy pass resets the ladder and sends one `default` "dispatcher recovered" ntfy.
3. **Escalation.** `bin/aq_escalate.py:11` gains a second rule: `title='DISPATCHER STALLED'
   AND status='open' AND count_since_last_recovery>=3` -> fleet-fires ticket with the recovery
   log attached + urgent page. Row 794 must be closed/reset when the ladder recovers so the
   count is per-incident, not lifetime (145 today).
4. **Distinct channel.** The stall page goes to lumen-alerts with title prefix `[DISPATCH]` and
   a `Tags: rotating_light` header so it is filterable from the sentinel floor. Frank's ntfy
   client can then route it to a sound the sentinel spam does not get.
5. **Self-test.** `dispatch_recover.py selftest` writes a synthetic unhealthy pass to a temp log,
   runs the classifier, runs rung 1 in dry-run, and asserts the page would fire. Wired into the
   Thursday gate (4) and `lumen-alert-hygiene-audit`.

Failure modes of the watchdog itself: health timer dead -> already covered by
`lumen-cron-stamp` deadman (unit ExecStartPost). ntfy down -> `ntfy-send` OOB fallback
(`bin/ntfy-send:56-62`). recover script crashes -> its own unit `OnFailure=` pages.

## 4. High-value-window uptime guarantee

The Max reset lands Thursday early AM Pacific. Design a pre-window gate that runs while there is
still time to fix things, and a during-window watch with a tighter K.

- `lumen-foreman-window-gate.timer`: `OnCalendar=Thu 00:30 America/Los_Angeles` (systemd
  timers honour a TZ suffix; verify with `systemd-analyze calendar` on this box, same
  requirement wq#1401 has for the maintenance window). Runs `dispatch_recover.py gate`:
  1. stamps fresh for every migrated repo, else run `harness-preflight` now (not at 03:00).
  2. `foreman_dispatcher.py run --dry-run` completes with `dispatchable>0` and
     `would_spawn>0`; if would_spawn=0, print the top skip reasons and page.
  3. governor state readable and `resets_at` in the next 6h; circuit breaker not tripped;
     DISPATCH_DISARM absent; `claude auth status` ok under the unit's environment (`systemd-run
     --pipe --property=Environment=PATH=<unit PATH> claude auth status`).
  4. every check passes -> one `default` "window gate OK" ntfy (so silence is not ambiguous).
     Any failure -> auto-recover via the ladder, re-check, then page urgent if still failing.
- During the window (Thu 01:00-09:00 PT) `dispatch_health.py` runs with K=1 and dedup 20 min,
  via a second timer instance passing `--threshold 1` (env-overridable already at :51).
- The same gate runs nightly at 22:00 PT for the ordinary overnight run; only the K/dedup
  tightening is Thursday-specific.

## 5. Test-to-failure plan

Each case is a script under `dispatcher/tests/` or `harness/tests/`, runnable against a temp
FOREMAN_ROOT, plus one live drill per case before the ticket closes. Pass criterion for all:
the pass completes with `pass_end`, other tickets still spawn (or would_spawn under dry-run),
and exactly the documented alert fires.

| case | inject | expected |
|---|---|---|
| (i) harness self-mod | commit that adds a hook to worker-settings.json and reorders base allow | `stamp_soft_stale` info event, dispatch proceeds, 0 reproves, 0 pages |
| (i-b) real permission change | add a rule to base.json allow | hard-stale, in-pass reprove succeeds, `auto_reproved` logged, spawns same pass |
| (ii) malformed ticket | ticket with archetype=lane name (wq#1358 shape) and, separately, a repos.d file with a JSON syntax error | one `bad_archetype` skip / one `profile_unreadable` skip per repo, rest of pass spawns, one ntfy |
| (iii) transient reprove failure | `FOREMAN_CLAUDE_BIN=/nonexistent` for attempts 1-2 via a shim that fails twice then succeeds | two retries logged, third succeeds, stamp written, 0 pages; with the shim failing 3x: one urgent page naming FileNotFoundError, cooldown set |
| (iv) rapid stamp churn | 5 profile commits in 10 min | at most 1 reprove per repo per pass (gate cache), no more than 1 page per repo per hour |
| (v) dispatcher crash | `kill -9` the pass mid-loop; separately `chmod 000 config.json` for one tick | no pass_end -> classifier unhealthy after 2 ticks -> ladder rung 1/2 runs -> healthy pass -> recovered ntfy. config case: rung 3 names config.json, fleet-fires ticket filed after rung 3 |
| (vi) watchdog self-test | `dispatch_recover.py selftest` weekly | synthetic stall pages in dry-run; failure of the selftest itself pages |
| (vii) window gate | run the gate with the timer PATH and a deliberately stale stamp | gate fixes it and reports OK |

Rollback for every piece: the new code paths are additive behind `FOREMAN_RECOVER_DISABLE=1`
(ladder), `FOREMAN_STAMP_STRICT=1` (restore full-dict hash), and the unit files can be
`systemctl disable --now`'d individually. DISPATCH_DISARM remains the universal off switch and
is never touched by any of this.

## 6. Build order (P0, one ticket each unless noted)

1. PATH/CLAUDE_BIN fix in harness_preflight + unit PATH + fworker.py:532 ntfy flag fix +
   `grep` test that no bare `"claude"` remains. (small, unblocks everything, ship first)
2. Per-ticket exception boundary + per-repo gate cache + wq() timeout + breaker inside try +
   preamble degrades. Tests for each row of 1.5.
3. Profile-only stamp digest + soft-stale dispatch + reprove retry ladder.
4. `dispatchable` in pass_end + classifier rule + dispatch_recover.py ladder + aq_escalate rule
   + `[DISPATCH]` channel + selftest.
5. Window gate timer + Thursday tightened watch.
6. Housekeeping surfaced by this audit, own tickets: `fleet` lane LANE_REPO or retire (33+
   tickets per pass are dead weight); passes running every 2-4 min against wq#1401 while its
   LAUNCH_FAILURE repeats (timer says 20 min; find the second caller); `dispatcher_health.py`
   retirement per self_heal.py:43-48; ntfy cache retention 12h is too short for incident
   forensics (raise to 7d or mirror to victorialogs).

Constraints honoured: no check is removed; a real permission change still reproves before
dispatch; every new degradation path emits an event and, where it persists, a page with a
specific cause; recovery is bounded (3 rungs, then escalate) and never touches DISARM.
