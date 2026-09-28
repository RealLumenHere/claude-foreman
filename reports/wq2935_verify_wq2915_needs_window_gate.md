# wq#2935 — adversarial verify-pass of wq#2915 (dispatcher: skip needs-window tickets outside the window)

Verifier: claude-sonnet-5 @ high, fresh instance. Worktree `wq2935-a2`. 2026-09-28.
Subject: PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1241, merged 741e75c3f4
(primary squash a2f4528, byte-identical diff — `git diff 741e75c a2f4528 -- dispatcher/
ops/wq1396-minerva-proposal-promoter/` is empty). Author: claude-sonnet-5 @ high.

## Verdict

**CONFIRM.** wq#2915's diff does what it claims: a new `NeedsWindow` gate in
`dispatcher/gates.py`, dark behind `needs_window.enabled` (default `false`), fail-closed on
any `lumen_window` load/read failure, correctly wired into the frozen gate order, plus a
`DISRUPTIVE_RE` marker step in the wq#1396 minerva promoter. No contradiction found.

## What the diff actually is

`git show 741e75c --stat` — 7 files, +257/-5: `dispatcher/config.json` (new
`needs_window` block, `enabled: false`), `dispatcher/foreman_dispatcher.py` (+63:
`_lumen_window_module`, `needs_window_marker`, `needs_window_reason`, `needs_window_cfg`,
`needs_window_enabled`), `dispatcher/gates.py` (+25: `NeedsWindow` class, spliced into
`GATES` between `Nodispatch()` and `FrankGate()`), `dispatcher/tests/test_gates_replay.py`
(`GATE_ORDER` updated to match), `dispatcher/tests/test_needs_window.py` (new, 135 lines,
7 tests), `ops/wq1396-minerva-proposal-promoter/minerva_proposal_promoter.py` (+`DISRUPTIVE_RE`,
`is_disruptive()`, title gets a `[needs-window]` prefix when disruptive), and its test file
(+2 checks).

## Checked against the diff cold

- **Real interface match**: `foreman_dispatcher.needs_window_reason` calls
  `mod.load_config()`, `mod.load_state()`, `mod.gate(cfg, state, name, now, est_sec=0,
  node=None, tags=(), release=None)`, `mod.OK`. Read the actual
  `/home/lumen/bin/lumen_window.py` (not a copy) — `gate()`'s real signature
  (`lumen_window.py:239`) and `OK` constant (`lumen_window.py:37`) match exactly. No drift
  between the fake-module test stubs and the real module's contract.
- **Window name**: `NEEDS_WINDOW_NAME = "fleet"` — checked against the live
  `/home/lumen/instructions/playbooks/maintenance-window.toml`: `[windows.fleet]` exists,
  `enabled = true`. Not a guessed name.
- **Fail-closed paths are exercised, not just asserted**: the diff's own
  `test_needs_window.py` covers module-missing and config/state-raises as two distinct
  fail-closed branches (`test_fail_closed_when_lumen_window_module_missing`,
  `test_fail_closed_when_lumen_window_config_unreadable`), both asserting `lumen_calls(denv)
  == []` — i.e. the gate genuinely blocks dispatch on failure, not just logs a soft warning.
- **Dark-ship contract**: `test_shipped_config_keeps_needs_window_dark` reads the *committed*
  `dispatcher/config.json` (not a fixture) and asserts `enabled is False`, and
  `test_dark_switch_ignores_marker_even_when_window_closed` proves the marker is fully inert
  when the flag is off — even with the window closed, the ticket dispatches. Matches the
  `decomposer.enabled` precedent cited in the docstring (checked: `decomposer_enabled` a few
  lines below in the same file uses the identical `block.get("enabled") is True` pattern).
- **Gate never crashes the pass on a malformed `lumen_window`**: `NeedsWindow.check()` has no
  try/except of its own around `mod.OK` comparison, but `foreman_dispatcher.py:2255`'s
  per-candidate `except Exception: ... "unhandled", "quarantined"` boundary (wq#1408 §3.1)
  catches anything `_gate_candidate` raises — a single malformed ticket/module never takes
  down the whole pass. Confirmed by reading the call chain, not assumed.
- **Regex scope** (`DISRUPTIVE_RE`): intentionally broad (`\bupgrade[ds]?\b`,
  `\breboot...`, `\brestart...`, `power.cycl`, `firmware`, `kernel`, `apt (dist-)?upgrade`,
  `systemctl/service restart`), documented in-line as "broad on purpose... a proposal wrongly
  held costs a Monday delay; one wrongly left on a live lane costs an outage." Tried to break
  it: it does NOT catch the test's own `p-feature` proposal ("Update Langfuse to v4.26.0...
  Bump the container tag.") — no "upgrade/restart/reboot" token appears, so a container bump
  that likely needs a restart is *not* tagged `[needs-window]`. This is a real classification
  gap, not a diff regression: nothing before wq#2915 tagged proposals at all, so an
  undercaught proposal dispatches exactly as it always did. Noting it, not contradicting on
  it — worth a follow-up ticket on the promoter's word list, not a block on this one.
- **Deploy guardrail**: the parent ticket's guardrails said to deploy any dispatcher restart
  through `lumen-window queue add ...` rather than restart live. Checked
  `_runs/wq2915/episode.jsonl` for any `lumen-window queue` invocation — none found; done
  notes say only "no live dispatcher restart." Since the shipped feature is dark
  (`enabled: false`), the live dispatcher running pre-wq2915 code is functionally
  indistinguishable from running post-wq2915-but-disabled code, so this is a deferred
  deploy step, not a correctness defect. AC1 (the ticket's only formal acceptance criterion,
  `_runs/wq2915/acceptance.json`) is a citation proof (`dispatcher/gates.py:261-278`), which
  is real code in a file the branch touches — satisfied.

## Reproduction (this pass, independent)

Confirmed `a2f4528` (in this worktree's history) is byte-identical to `741e75c` for the
touched paths, then ran the diff's own tests directly against the live tree (no
re-implementation):

- `pytest dispatcher/tests/test_needs_window.py -v` — **7 passed**.
- `pytest dispatcher/tests/test_gates_replay.py -q` — **29 passed** (includes
  `test_gates_are_the_dissection_order`, which would fail if `NeedsWindow`'s splice point
  broke the frozen order).
- `python3 ops/wq1396-minerva-proposal-promoter/test_minerva_proposal_promoter.py` — **all
  15 checks passed**, including the two wq#2915-added ones
  (`disruptive (upgrade) proposal gets the needs-window marker`,
  `non-disruptive proposal has no needs-window marker`).

Matches the PR's own done-notes claim ("150+42+7 tests green"); this pass reproduced the
`+7` (test_needs_window.py) and the relevant slices of the other two suites directly.

## Gate (this pass, final state)

- `pytest dispatcher/tests/test_needs_window.py -v` — 7 passed.
- `pytest dispatcher/tests/test_gates_replay.py -q` — 29 passed.
- `python3 .../test_minerva_proposal_promoter.py` — all checks passed.
- Interface cross-check against the real `/home/lumen/bin/lumen_window.py` and the real
  `maintenance-window.toml` — no drift.

## Board

wq#2915 verify-pass: **CONFIRM**. No redirect, no escalation to opus-5@high judgment. One
non-blocking observation (promoter's `DISRUPTIVE_RE` word list can under-tag a disruptive
proposal like a bare version-bump with no "upgrade/restart/reboot" token) and one
non-blocking deploy-completeness note (window-queue restart never queued) are recorded
above for a possible follow-up ticket; neither contradicts the diff's core claim.
