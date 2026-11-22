# wq#5323: verify of wq#5145 (review-readonly permission profile + SARIF)

Subject: PR#2749 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2749), merge bb6e89ce12.
Verified at worktree HEAD 347ab1b5 (includes bb6e89ce12 and the later ccbd1070 fworker change).
Verifier: fresh instance, archetype 8, adversarial.

Verdict: **CONTRADICT**. Per ticket policy this escalates to an opus-5-5@high judgment.

## What holds

- `python3 -m pytest -q harness/tests/test_review_readonly.py harness/tests/test_agents_spec.py harness/tests/test_permission_profiles.py`: 57 passed.
- Bash, Write and Edit are denied by name in the rendered pack. `fworker_sdk.Permissions.check` returns
  `denied by permission rule Bash` / `Write` / `Edit` (probe below). Hooks and `defaultMode` pass through unchanged.
- Router: `router.py assign 5145 --archetype review-readonly --dry-run` resolves the archetype and picks the security chain
  (`security_exclusion`, opus primary). Archetype 11 does not collide with any other number.
- Close path: `acceptance.derive` pins no criteria for the archetype (acceptance.py:466), so the stop gate's evidence
  branch (stop_gate.py:89) and `verify_done` (fworker.py:5459) both pass through. `close_review_readonly` runs before the
  sentinel and exit gates (fworker.py:5052). The in-session exit gate is disarmed for this archetype (fworker.py:4864).
  The post-session gate sees ticket_done + TICKET_CLOSED, which is 2 indicators. `pin_acceptance` receives the resolved
  archetype (fworker.py:4757), so a router-assigned archetype is handled the same way as one set in the ticket column.
- review_sarif: the last fenced block wins, levels are restricted to the four SARIF values plus aliases, lines are 1-based,
  `end >= start` is enforced, and the output files are written 0600 via tmp+replace.

## Contradiction: the read-only pack has a weaker credential deny floor than a build worker

`render_review_readonly(base_settings)` (permission_profiles.py:130-141) builds its deny list from
`fworker.SETTINGS_FILE`, which is the static `harness/worker-settings.json` (fworker.py:2940-2941). It then replaces the
per-repo union "outright". The problem is that `worker-settings.json`'s deny list is stale. It does not contain the four
credential-copy Read rules that `permission-profiles/base.json` added in 3f0c7c3c ("deny credential copies inside
granted dirs"):

- `Read(**/claude_credentials.md.*)`
- `Read(/home/lumen/minerva/context/sections/*credentials*)`
- `Read(/home/lumen/minerva/context/sections.json)`
- `Read(/home/lumen/minerva/context/sections.json.*)`

A build worker on foreman, pokemon or mailbot (the repos with a repos.d entry) gets base.json's deny list as a floor
(permission_profiles.py:74-82). The review worker on the same repo does not.

This matters most for Grep and Glob. guard.sh has a credential branch that covers `Read|Edit|Write|Grep|Glob`
(guard.sh:682-692), but the PreToolUse matcher that invokes guard is `Bash|Read|Edit|Write` (worker-settings.json:138),
and fworker_sdk.py:299 matches it with `re.fullmatch`. guard.sh therefore never runs for Grep or Glob. For those two
tools the settings deny rules are the only control, because Read rules govern Grep and Glob. This is the same failure
wq#1267 named: a floor that drops protections guard.sh does not independently re-implement. It also lands on the one
archetype whose entire capability is reading. The review pack allows bare `Read`, and `Permissions.check` honours an
allow match before it checks working dirs (fworker_sdk.py:272-273), so these paths are reachable from any cwd.

Probe (committed: `reports/wq5323_probe_review_pack.py`). It only calls `Permissions.check()`, never opens the target
paths, and assembles the path literals at runtime:

```
$ python3 reports/wq5323_probe_review_pack.py /home/lumen/worktrees/wq5323-a1
Grep ...erva/context/sections.json review=True  build=False  <-- REGRESSION
Glob ...erva/context/sections.json review=True  build=False  <-- REGRESSION
Read ...erva/context/sections.json review=True  build=False  <-- REGRESSION
... (sections.json.bak, sections/x-credentials.md, claude_credentials.md.bak: same)
Read ...text/claude_credentials.md review=False build=False
Bash  (False, 'denied by permission rule Bash')
is_review_readonly: True
regressions: 12          (exit 1)
```

The probe can fail: it exits 0 as soon as the review pack carries base.json's denies. It compares against the foreman
build pack, which is an input from outside the review pack's own default population. Read on the first three path
families is also backstopped by guard.sh, so the unbackstopped exposure is 8 of the 12 pairs (Grep and Glob × 4).

`is_review_readonly()` (permission_profiles.py:144-152) returns True for this pack. It checks only the exec and write
denies, so a consumer like mcp-guard that trusts it would accept the weaker floor.

## Fix for the judgment / rework lane (not implemented here, verify-only)

1. In `render_review_readonly`, union `permission-profiles/base.json`'s deny into the pack next to the base settings'
   deny.
2. Make `is_review_readonly` also require base.json's `Read(...)` denies.
3. Add a test that diffs the review pack's deny list against base.json's deny list.
4. Separately, consider widening the guard.sh PreToolUse matcher to `Bash|Read|Edit|Write|Grep|Glob` so its existing
   Grep/Glob branch actually runs.

## Minor (non-blocking)

- No test drives the `do_attempt` wiring at fworker.py:4864/5052. Only `close_review_readonly` is unit-tested.
- review_sarif does not reject absolute or `../` finding paths. They are emitted with `uriBaseId: SRCROOT`, which is
  metadata only, so this is cosmetic.

ISSUE-LOG row #1743.
