# wq#3310 verify: wq#3287 model-watchdog pages 'model DOWN' x4 during a planned litellm recreate

Archetype 8 adversarial verify pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5
(fresh instance, same model as author per ticket routing).

PR: lumen/claude-foreman#1519, landed on `main` as `cd2fd04c35960f6e2b19520c0f5b14eb21aa72e9`.
Files: `ops/wq2903-model-ref-drift/lumen_model_watchdog.py` (+35/-2),
`ops/wq2903-model-ref-drift/tests/test_watchdog_drift.py` (+49). No other files touched.

## What wq#3287/PR#1519 claimed

A planned `litellm` docker recreate (2026-10-01 03:10:32Z, healthy again 03:11:00Z) paged
aq rows 3055-3058 (cerebras-llama, cerebras-oss-120b, deepseek-v4-flash, deepseek-v4-pro)
because the in-run retry gap was 3s — far shorter than the recreate's own outage window —
so both probes for each rung landed inside the outage. Fix ships two independent
mitigations: (1) widen the retry gap from 3s to `RETRY_DELAY_S` (default 30s, env-tunable
via `MODEL_WATCHDOG_RETRY_DELAY_S`), and (2) add `slock_held("litellm")` — if a probe still
fails after retry and the `litellm` slock is held (operator mid-maintenance), carry forward
the previous up/down state instead of alerting, rather than treating a still-failing probe
as a verdict. Recovery still auto-resolves the aq row on the next clean pass via the
existing `aq_clear` edge, unchanged. Claimed 20/20 tests pass incl. 3 new.

## What I checked

- Read `lumen_model_watchdog.py` cold: the new constants (`RETRY_DELAY_S`, `SLOCK_BIN`,
  `MAINTENANCE_SCOPE`), `slock_held()` (line 202), and the retry/maintenance-skip/local-busy
  stack in the per-model probe loop (lines 309-336), plus the unchanged DOWN/recovery edge
  logic below it (lines 338-348).
- Ran the full ops-bundle test suite: `python3 -m pytest
  ops/wq2903-model-ref-drift/tests/ -q` → **20 passed**, matching the claim exactly
  (11 in `test_watchdog_drift.py` alone, 20 across all three test files in the bundle).
- Confirmed `"litellm"` is the house canonical slock subsystem name, not an invented one:
  `/home/lumen/bin/wq:9` documents it as part of the "Canonical subsystem vocabulary
  (2026-07-17): vault-ha, litellm, minerva-db, ...", and
  `research/DISPATCH_GOVERNOR_WALKTHROUGH.md:177` references a real prior incident
  ("wq#1623 spawned 6 times: fworker CLAIM_ERROR because subsystem `litellm` is slocked by
  work-1580") — so operators/workers already hold this named lock for litellm-affecting
  work; the guard isn't relying on a convention that doesn't exist.
- Verified the `slock_held()` parsing against the **real** binary, not just the mocked
  tests: `/home/lumen/bin/slock who litellm` → `(no active locks)`, exactly the substring
  `slock_held()` checks for. Then imported the live deployed module directly (not through
  the test harness) and called it end to end:
  ```
  $ python3 -c "
  import sys; sys.path.insert(0, '.../ops/wq2903-model-ref-drift')
  import lumen_model_watchdog as wd
  print(wd.slock_held('litellm'), wd.RETRY_DELAY_S, wd.SLOCK_BIN)
  "
  False 30 /home/lumen/bin/slock
  ```
  Matches expected real-world behavior (no lock held -> False; default retry 30s; resolves
  the real `/home/lumen/bin/slock` binary), not an artifact of the test mocks.
- Traced the full control flow for both failure classes the fix must distinguish:
  - Still-down-with-no-slock after retry -> `slock_held()` False -> `ok` stays False ->
    DOWN edge fires (`test_retry_waits_the_widened_gap_before_a_verdict` pins
    `RETRY_DELAY_S >= 30` and asserts the real page still happens — a falsifiable probe,
    not one that only confirms the happy path).
  - Still-down-with-slock-held -> `was_up = prev.get(model, True)`; if the model was
    previously up, `ok` becomes `True` and no DOWN edge fires, state carries forward; if the
    model was already down before maintenance started, `ok` stays `False`, but no new aq
    row opens either, because the DOWN-edge check requires `not ok and was` (line 341) —
    `was` is already `False`, so this isn't a new edge, consistent with existing (pre-wq#3287)
    behavior of not re-opening an already-open row.
  - Recovery after maintenance: unchanged `aq_clear` edge at line 347-348 still fires the
    first time a clean probe follows a carried-forward-or-real down state.
- Checked for resource/scheduling fallout from the widened sleep: `lumen-model-watchdog` is
  `OnUnitActiveSec=10min` oneshot (`research/FLEET_SERVICE_ATLAS.md:178,370`). Worst case
  (every cloud rung down at once) is N×30s of added sleep; for the fleet's roster size that
  is well inside the 10-minute cadence, not a new starvation/overlap risk.

## What I did not find broken

- No regression in the pre-existing 17 tests across the bundle; all 20 pass, independently
  reproduced (not just taking the PR's own number on faith).
- The two mitigations are genuinely independent, matching the commit message's own framing:
  the retry widening alone would likely have absorbed this specific ~38s-recovery incident;
  the slock guard is a separate backstop for recreates that outlast even the widened retry,
  and does not depend on timing at all as long as the lock is held for the duration.
- `slock_held()` fails soft (`except Exception` -> `False`, i.e. "assume not held, let a
  real down page") rather than fail-open into silently swallowing real outages — correct
  direction for a maintenance-skip guard. Covered by
  `test_slock_held_reads_slock_who_output`'s third case and verified the real subprocess
  call shape independently above.
- No single-miss DOWNs: the pre-existing 2-consecutive-probe discipline (wq#532/Ma'at
  lesson, line 15 docstring) is preserved; the new gap only changes the interval between
  the two probes, not the count.

## One limitation noted, not a contradiction

Mitigation #2 depends on whoever performs a "planned litellm recreate" actually holding the
`litellm` slock for its duration — I did not find a dedicated recreate wrapper script in
`ops/` or `bin/` that acquires this lock automatically; it's operator/workflow discipline
(matching how `litellm` is used as a slock scope elsewhere in the fleet per wq#1623), not a
system invariant the code can enforce. If the 2026-10-01 03:10:32Z incident's recreate was
run without the lock held, mitigation #2 would not have engaged for that specific incident
and the fix's full protection rests on the 30s retry widening alone. This is an auditable
gap in the human workflow around `litellm` maintenance, not a bug in this diff — the
commit message already frames the two mitigations as independent/redundant rather than
claiming the slock guard alone is sufficient. Not filing a follow-up; flagging for the
record in case the fleet wants to wire the lock acquisition into whatever a "recreate"
actually is (manual command vs. a script).

## Ops bundle liveness (`ops_verified(wq2903-model-ref-drift)`)

Could not execute `bash ops/wq2903-model-ref-drift/install.sh --verify` as the ticket's
Ops-bundle liveness line directs: the command is not on the Bash allowlist (`Bash(bash
scripts/*)` does not match an `ops/` path), and even invoking the executable directly hits
the same "requires approval" refusal with no human present to approve it. Manually
replicating the verify script's checks also failed: its live targets
(`/home/lumen/agents/model-watchdog/lumen_model_watchdog.py`, `/etc/systemd/system/...`,
`/home/lumen/agent/lumen_quota_manager.py`, `/home/lumen/minerva/model_audit.py`) sit
outside this session's granted directories
(worktree + work-queue/mailbot/pokemon/bin/foreman/kb/projects-nest/tmp), so `diff`/`cmp`
against them is blocked by the sandbox, not by anything the ticket's `--verify` would
itself report. I did confirm the one piece inside my granted dirs that `--verify` would
also check — the repo's shipped `lumen_model_watchdog.py` matches what I reviewed and
tested above — but I cannot independently reproduce the full exit-0 liveness claim from
this worktree. Naming the exact refused command shape for the allowlist:
`bash /home/lumen/foreman/ops/<bundle>/install.sh --verify` (and the direct-executable
equivalent) is not currently runnable by a headless build worker.

## Conclusion

**CONFIRMED.** wq#3287's fix does what the ticket asked: the model-watchdog's in-run retry
now outlasts a routine litellm recreate (30s, was 3s, env-tunable), and a slock-held
backstop independently prevents a still-failing probe from being treated as a verdict
during acknowledged maintenance, without weakening the existing 2-consecutive-probe DOWN
discipline or the existing recovery auto-clear. 20/20 tests pass, independently reproduced;
the `slock_held()` parsing was additionally checked against the real `slock` binary, not
just its test mocks. No contradiction found; no escalation to opus-5@high needed.

Action taken: no code changes (verify-only per ticket instruction). Closing wq#3310 as
confirmed via `wq done`.
