# wq#1422 — Adversarial verify-pass of wq#1410 (dispatch watchdog actor)

**Verdict: CONTRADICT** — the mechanics (ladder rungs, state machine, hung-pass
detection, alarm-window fix, selftest, ticket-filing regex) are all correct and the
shipped test suites pass for the right reasons. But the new `dispatchable` metric —
the entire point of wq#1410 §1.6/§3.3.1 is "a queue of structurally-dead candidates
must not look like a stall" — has a real gap: two of the skip reasons that are exactly
as "never really dispatchable without a human" as `frank_gated` (which the ticket
already treats as structural) were left out of `STRUCTURAL_SKIP_REASONS`. A backlog
consisting entirely of fire tickets awaiting Frank's approval or missing a fire probe
is misclassified as real, spawnable backlog, which will drive `dispatch_health.py` to
declare a false stall and `dispatch_recover.py` to run a futile
preflight/restart/diagnose ladder and eventually page urgent + auto-file a fleet-fires
ticket for a "stall" that only Frank can resolve — the exact failure mode this ticket
was filed to eliminate, reintroduced via a different pair of reason strings.

## What was shipped

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/290 (branch
`feat/wq1410-dispatch-watchdog-actor-recovery-ladder--a2`, head `88dbab3`). **Note:**
this PR is open/unmerged — it lost a duplicate-dispatch race to a sibling worktree
(`-a1`, see `/home/lumen/kb/ISSUE-LOG.md` row 170) whose independent implementation of
the same ticket landed on `main` as `43e1e45` first; `lumen-pr land` correctly refused
`-a2` as NOT MERGEABLE. Per this ticket's framing ("the diff shipped by wq#1410 —
PR#290") I verified PR#290's actual diff (`git diff <merge-base 2a63311>..origin/...-a2`,
6 files, +858/-14: `dispatch_health.py`, new `dispatch_recover.py`,
`foreman_dispatcher.py`, 2 test files, `evidence.json`). I also diffed it against the
landed `-a1` version: two genuinely independent implementations of the same spec, not
near-duplicates. **The bug below is present in both** — `-a1`'s
`STRUCTURAL_SKIP_REASONS` (foreman_dispatcher.py:109-112, currently on `origin/main`)
is byte-identical to PR290's set, so this is not a defect specific to the losing PR;
it reflects a gap in wq#1410's own ticket spec, faithfully implemented by both authors.

## Mechanics — CONFIRMED

Extracted PR290's branch into a full detached worktree (`git worktree add --detach`)
and ran its own suites unmodified:
- `dispatcher/tests/test_dispatch_recover.py`: 20/20 pass.
- Full `dispatcher/tests/` (`test_dispatcher.py` + others, needs `orchestrator/router.py`
  etc. on `sys.path`, which `foreman_dispatcher.py` sets up itself): 211/211 pass.
- `python3 dispatch_recover.py selftest`: `SELFTEST PASS` (synthetic 2-pass stall fires
  in dry-run, rung-1 dry-run reports ok).

Verified correct, adversarially:
- The alarm-window bug this PR fixes (`all(...)` over an empty `recent` list was
  vacuously `True` pre-PR) is genuinely fixed: `len(recent) == threshold and all(...)`.
- `dispatch_recover`'s `RUNGS`/`MAX_RUNG` bookkeeping, `FOREMAN_RECOVER_DISABLE=1`
  no-op path, `dry_run` never persisting/never paging, and the escalate-once-then-wait
  branch (`st.get("escalated")`) all behave as documented — traced by hand and by the
  20 unit tests, which cover each rung, disable flag, reset-with/without-progress, and
  the escalation ticket-id regex against `wq add`'s actual two output shapes
  (`grep -n '"added task\|DUPLICATE'` in `/home/lumen/bin/wq` — both match).
  `dispatch_recover.py` imports cleanly as a script (implicit `sys.path[0]`), and
  `check_health()`'s in-function `import dispatch_recover` resolves the same way when
  invoked as `python3 dispatch_health.py check` (its actual systemd `ExecStart=`).
- The ladder advancing once per **health-check tick** (5-min timer,
  `lumen-foreman-health.timer`, confirmed `enabled`+`active` on this host) rather than
  once per **dispatcher pass** (20-min timer, `lumen-foreman-dispatch.timer`, also
  `enabled`+`active`) looks alarming at first read — it means, once alarmed, the
  3-rung ladder exhausts and escalates within ~15 minutes without ever letting a fresh
  dispatcher pass re-score rung 1's fix before trying rung 2. But this is the
  literal, explicit design in `research/NIGHTLY_DISPATCH_RESILIENCE.md` §3.3 ("bounded
  ladder, **one rung per health tick**") — confirmed intentional, not a bug.
- `rung2`'s `systemctl restart lumen-foreman-dispatch.timer` / `start .service` cannot
  kill its own caller: `dispatch_health.py check` runs under the separate
  `lumen-foreman-health.service`, not the unit it restarts.

## Contradiction — `dispatchable` undercounts human-gated backlog

`STRUCTURAL_SKIP_REASONS` (`dispatcher/foreman_dispatcher.py`) is:

```
{"lane_unmapped", "nodispatch_marker", "frank_gated", "deps_unmet",
 "runaway_stuck", "not_open", "vanished"}
```

`dispatchable = candidates - structural_skips()` feeds `dispatch_health.py`'s alarm
rule (`spawned==0 and dispatchable>0` ⇒ unhealthy). Two skip reasons that are
**conceptually identical to `frank_gated`** — a candidate that is stuck on a human
action, not on anything the dispatcher/ladder can fix — are missing:

- `fire_unapproved` (`foreman_dispatcher.py:547`, `handle_unapproved_fire`): "dispatch-
  ready but has no approval file… To approve: touch {path}" — literally requires
  Frank to `touch` a file.
- `fire_no_probe` (`foreman_dispatcher.py:572`, `handle_no_probe_fire`): "a fire ticket
  without a probe is refused dispatch… a FRANK page, not a worker" (the code comment's
  own words).

### Reproduction (against PR290's unmodified `foreman_dispatcher.py`)

```python
import foreman_dispatcher as fd
log = fd.DecisionLog("/dev/null", "demo")
for i in range(3):
    log.skip(1000 + i, "ops", "fire_approval", "fire_unapproved", detail="no approval file")
for i in range(2):
    log.skip(2000 + i, "ops", "fire_probe", "fire_no_probe", detail="no probe defined")

log.structural_skips()          # -> 0
max(0, 5 - log.structural_skips())   # -> dispatchable = 5, candidates = 5, spawned = 0
```

Output: `dispatchable=5` for a pass whose entire backlog is fire tickets sitting on
Frank's desk. Two such passes (`DEFAULT_THRESHOLD` is now 2, not 3, per this same PR)
trigger `dispatch_health.py`'s STALL alarm, which drives `dispatch_recover.advance()`
through `harness-preflight --all` → `systemctl restart` the dispatch timer/service →
config diagnose → urgent page + auto-filed fleet-fires ticket, none of which can
possibly clear an unapproved-fire-ticket backlog. This is exactly the "structurally-
dead candidates masking/triggering a stall signal" failure wq#1410 was filed to kill
(aq#794, count=145 since 08-23) — reintroduced through the one pair of reasons the
enumerated set missed.

### Why the shipped tests don't catch it

`dispatcher/tests/test_dispatcher.py`'s new coverage
(`test_dispatchable_excludes_structural_skip`,
`test_dispatchable_counts_non_structural_skip_as_real_backlog`) only exercises
`lane_unmapped` (correctly excluded) and `lane_cap` (correctly included). `fire_
unapproved`/`fire_no_probe` are asserted elsewhere (lines ~453, ~497) purely as skip
*events*, never against `dispatchable`/`structural_skips()`. No test puts a fire-gated
candidate through the `dispatchable` computation, so the gap has no failing test to
catch it.

## Scope note

`STRUCTURAL_SKIP_REASONS` in PR290 is byte-identical to the version on `origin/main`
(landed via the sibling `-a1` branch, `43e1e45`) and to current `origin/main` tip
(`6d981ba`) — this is not something PR290 got wrong relative to `-a1`; both authors
implemented wq#1410's own enumerated list verbatim
(`wq show 1410`: "candidates minus structural skips (lane_unmapped, nodispatch_marker,
frank_gated, deps_unmet, runaway_stuck, not_open, vanished)"). The gap is in that
enumeration, not in either diff. Separately: the primary checkout at
`/home/lumen/foreman` that the live systemd units actually execute from is currently
14 commits behind `origin/main` (missing `43e1e45` entirely — `dispatch_health.py` on
disk still reads `DEFAULT_THRESHOLD = 3` with no `dispatch_recover` import), so none
of this — correct or buggy — is live yet. It will be the moment that checkout is
synced to current `main`.

## Disposition

Fix is small and additive: add `"fire_unapproved"` and `"fire_no_probe"` to
`STRUCTURAL_SKIP_REASONS` in `dispatcher/foreman_dispatcher.py` (one line, both
implementations), plus a test mirroring
`test_dispatchable_excludes_structural_skip` for the fire-gated case. Not
re-implementing here per verify-pass scope — filing this as a judgment escalation for
the fix to land as its own change, and flagging it against whichever ticket ends up
owning the fix (main already carries the same gap via `-a1`/`43e1e45`).
