# wq#1459 — verify-pass: wq#1411 Thursday Max-reset window gate + tightened overnight watch

**Verdict: CONTRADICT** — `gate()`'s own worst-case runtime, on the exact failure
class it exists to catch, can exceed `lumen-foreman-window-gate.service`'s
`TimeoutStartSec=600`. There is no `OnFailure=` on the unit, so a timeout kill
produces **zero** ntfy — neither the "OK" nor the "FAILED" the gate's own docstring
promises ("silence is never ambiguous with didn't run"). Everything else in the
diff holds up: 33/33 shipped tests pass, all five checks are wired to real,
correct data sources, and the live probe run reproduces the author's own
skip/failure-reason formatting exactly.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/324 (merged, commit
`6764a84` / merge `3d43565`, branch `feat/wq1411-window-gate`).

## What was verified and holds up

- `pytest dispatcher/tests/test_dispatch_recover.py` — 33/33 pass, unmodified, on
  a worktree checked out at the exact merged commit.
- Ran the shipped `dispatch_recover.py gate --dry-run` live (unmocked) against
  this box: it correctly read the real `permission-profiles/repos.d/` (3 migrated
  repos: foreman, mailbot, pokemon), ran the real `foreman_dispatcher.py run
  --dry-run` and parsed its `pass_end` event for `dispatchable`/`spawned` exactly
  as `dispatch_recover.py:376-384` claims, read `circuit_breaker.evaluate()` for a
  live not-tripped verdict, and attempted the real `systemd-run --pipe
  --property=Environment=PATH=...` `claude auth status` call (failed here only
  because this headless worker's session lacks polkit interactive-auth rights —
  an environment limit of this verify session, not a code defect: the call shape
  matches §4 item 3d exactly).
- `_gate_check_governor`, `_gate_check_breaker_disarm` field names
  (`stats['failures']`/`['total']`/`['window_minutes']`) match
  `circuit_breaker.evaluate()`'s real return shape
  (`dispatcher/circuit_breaker.py:116-117,142`).
- `lumen-foreman-window-gate.service`'s `Environment=PATH=` matches
  `lumen-foreman-dispatch.service`'s byte-for-byte, which is what
  `DISPATCH_UNIT_PATH`'s docstring (`dispatch_recover.py:86-90`) requires.
- `dispatch_health.py`'s `FOREMAN_HEALTH_ALERT_DEDUP_SECONDS` override is
  backward-compatible (defaults to the existing 3600 unchanged) and threaded
  correctly into `lumen-foreman-health-thu.service`.

## Contradiction — no timeout margin, no `OnFailure=`, on the exact scenario the gate exists to catch

`gate()` (`dispatcher/dispatch_recover.py:487-533`) runs `_run_gate_checks()`
once, and on any failure runs `advance()` (one ladder rung) then
`_run_gate_checks()` again before deciding OK-after-recovery vs. urgent page.
Each check function's own bound, un-mocked:

| check | bound | citation |
|---|---|---|
| `stamps_fresh` (live reprove loop, **no aggregate cap**) | `LIVE_PROOF_TIMEOUT=300s` **per stale repo**, sequential | `harness/harness_preflight.py:64`; loop at `dispatch_recover.py:343-348` |
| `dispatch_dry_run` | `timeout=580` | `dispatch_recover.py:357` |
| `governor_resets` / `breaker_disarm` | file/in-memory reads, ~0 | `dispatch_recover.py:397-448` |
| `claude_auth` | `timeout=60` | `dispatch_recover.py:462` |
| ladder rung 1 (`harness-preflight --all`) | `timeout=580` | `dispatch_recover.py:152` |

Right now, live on this box, there are 3 migrated repos
(`ls harness/permission-profiles/repos.d/*.json` → 3), and the dry-run probe
above found all 3 currently stale. A real (non-dry) `gate()` run today would
sequentially live-reprove all 3 — up to `3 × 300s = 900s` — for the
`stamps_fresh` check **alone**, before `dispatch_dry_run`'s own `580s` ceiling
or `claude_auth`'s `60s` are even reached. One pass through `_run_gate_checks`
already has a worst-case bound near **1540s** — 2.5× the service's own budget —
with zero contribution from the ladder or the recheck.

`gate()`'s failure branch runs this same up-to-~1540s check pass **twice**
(initial + recheck) plus one ladder rung (`≤580s`) in between — a worst-case
north of **3600s (~60 min)** for a single invocation, against
`lumen-foreman-window-gate.service:20`'s `TimeoutStartSec=600` (10 min), a
6× shortfall with no incremental safety margin.

This isn't a rare edge case — it's the scenario `gate()` exists to catch: a
wedged unit or a batch of newly-stale stamps (research/NIGHTLY_DISPATCH_RESILIENCE.md
§1.4/§1.5, and the doc's own test-plan case (i-b): "add a rule to base.json
allow" → hard-stale **every** repo at once) is exactly when `stamps_fresh` and
`dispatch_dry_run` are slowest, and exactly when the compounded worst-case above
is reached. `lumen-foreman-window-gate.service` has no `OnFailure=` directive
(confirmed: `grep OnFailure dispatcher/systemd/*.service` → no hits on any
currently-shipped unit). If systemd kills the process on `TimeoutStartSec`, the
Python process dies before either `_ntfy(... GATE_OK_TITLE)` or
`_ntfy(... GATE_FAIL_TITLE)` is reached — no ntfy at all, and per systemd
semantics `ExecStartPost` does not run for a start job that timed out, so even
the `lumen-cron-stamp` dead-man's-switch stamp (wq#1184) is skipped. The one
guarantee `gate()`'s own docstring makes — "All five checks pass -> one
default-priority... ntfy, so silence is never ambiguous with 'didn't run'" —
is the exact thing this defeats.

(This does not leave the fleet fully blind: the pre-existing, independent
`lumen-foreman-health.timer` / `dispatch_health.py` log-tail classifier — wq#1410,
unchanged by this PR — would likely still catch a genuinely stalled dispatcher
within its own ~2-tick window and page through that separate path. What's lost
is specifically the early, "still time to fix it" pre-window signal wq#1411 was
opened to add, and the explicit OK/FAILED gate ntfy it promises.)

## Independent corroboration already in flight, unmerged

The same branch (`feat/wq1411-window-gate`) has one further commit,
`aa3bf25` ("feat(dispatcher): pre-window dispatch gate + Thursday tightened
watch", same `Refs: wq#1411`), pushed seconds after the merge and still open as
PR#325 — not referenced by wq#1411's own "done" notes, not reviewed, not part of
what this verify-pass was asked to check. It is a substantial rework (721-line
diff to `dispatch_recover.py` alone) that, notably, bumps
`lumen-foreman-gate@.service`'s `TimeoutStartSec` to `1500` and adds
`OnFailure=ntfy-onfailure@%n.service` — i.e., it independently touches exactly
the two knobs this report flags. I did not evaluate PR#325's diff for
correctness (out of scope: it is unmerged, untracked by any wq ticket I could
find, and not the artifact wq#1459 was asked to verify) — flagging its
existence only so whoever picks up the fix-shape judgment call knows unreviewed
work already exists in this area instead of starting from zero.

wq#1411 itself also shipped without its own stated "Done:" criterion met — the
ticket's acceptance line reads "gate ran unattended once and its OK ntfy is in
lumen-alerts," and the closing notes honestly disclose this was never
demonstrated ("systemd-analyze/systemd-run not on worker allowlist ... await
Frank's enable step"). Noted for completeness; not treated as part of this
CONTRADICT since it was disclosed, not fabricated (instinct 7 n/a here).

## Disposition

The check logic, data wiring, and PATH/env consistency are all correct. The gap
is purely in the unit's time budget vs. the code's own worst-case, and the
missing `OnFailure=` fallback. Fix shape is a judgment call (raise
`TimeoutStartSec` to cover the true worst case incl. migrated-repo-count growth,
and/or add a per-check or aggregate deadline inside `gate()` itself so it fails
fast and still ntfy's rather than running out the clock, and/or add
`OnFailure=`) — not mine to make here per verify-pass precedent (wq#872/#1268,
wq#1433/#1434). Not filing the fix myself. Escalating to opus-5@high.
