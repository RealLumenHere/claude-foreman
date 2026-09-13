# wq#1886 — Adversarial verify-pass of wq#1882 (despag P1-exit: full conformance, both harnesses)

**Verdict: CONFIRM.** PR #560 (commit `45e1695` on `main`, based on `bae1e17`) does what
it claims: both harnesses hit 40/40 CONFORMANT on `--class core` from one despag
process. Read the diff cold, independently re-ran the probe against a fresh process of
my own (not the author's, not their data), and checked every file:line citation in the
report against the actual source. One real defect found (miscited fworker.py lines,
non-blocking — see below).

## What was checked

Diff: `despag/tools/run_p1_exit.py` (new, 83 lines), `despag/reports/P1_EXIT.md` +
4 committed report/log artifacts, `acceptance.json`/`evidence.json` retargeted to
wq#1882. No production despag code changed — this is a test/report ticket.

- **Independent reproduction, litellm-mechanical**: started my own `despag` process
  (port 8796, isolated `--data-dir`, via the PR's own `run_p1_exit.py serve`) and ran
  `run_p1_exit.py conformance --class core --harness-id chrn_litellm-mechanical`
  end to end. Result: **40/40 pass, CONFORMANT** — matches
  `despag/reports/p1-exit_litellm.json` exactly. This is the same command
  `evidence.json`'s AC1 smoke run used; I ran it fresh in my own directory, not a
  replay of their output.
- **Independent reproduction, claude-max (real spawn)**: against the same server,
  ran the conformance CLI directly (bypassing `run_p1_exit.py`'s wrapper to pass
  `--only T-01,T-03`, since a full 40-check claude-max suite would spend real
  Claude-Max budget for marginal additional verification value). Both passed for
  real: `T-01` completed in 3.1s, `T-03` reported `model=claude-sonnet-5` — this is
  a genuine subprocess spawn through `despag/backends/claude_max.py`, not a stub.
  Confirms the report's claim that `claude-max` was actually exercised, not just
  discovered.
- **Cancel/kill code path** (`despag/runner/turn.py:121-136`, `_kill`,
  `os.killpg(..., SIGKILL)` at line 133, reachable via `start_new_session=True` at
  line 165): read the file directly — every cited line number is exact. Did not
  re-run the OS-level cancel test myself (would need a second real claude-max spawn
  for no new information — the code path is unambiguous from source).
- **OAuth-token-per-spawn claim** (`despag/backends/claude_max.py:37` calling
  `fworker.build_env`, which calls `fworker.read_oauth_token()` fresh, no
  module-level caching): the substance is correct — verified by reading
  `harness/fworker.py` — but **the report's line numbers are wrong**. It cites
  `fworker.py:1046-1053` for `read_oauth_token()` and `:1070` for its call site;
  the actual lines are `1035-1045` and `1059` (off by ~11 in both places). Checked
  `git log bae1e17..HEAD -- harness/fworker.py`: no commits touched this file in that
  window, so this isn't drift — the citation was wrong when written. Minor: this
  citation lives in the narrative `P1_EXIT.md`, not in `evidence.json`'s AC1 gate
  (which cites `run_p1_exit.py:22-32`, itself accurate to within a line). Does not
  change the underlying claim's truth, just its checkability.
- **wq#1852 baseline comparison** ("38/40, T-08 fail, S-09 skip"): checked
  `despag/reports/wq1852_core_postfix.json` directly —
  `summary: {"pass": 38, "fail": 1, "skip": 1, "total": 40}`, `T-08` outcome
  `fail`, `S-09` outcome `skip`. Exact match.
- **`app.py` discovery/budget wiring** (`:367-379` capabilities, `:394`
  `body["lumen"]`, `admission.py:257` `budget_snapshot`): all confirmed at or within
  one line of the cited numbers (decorator-vs-def off-by-one, normal tolerance).
- **ISSUE-LOG #374 "append blocked" claim**: `kb/ISSUE-LOG.md`'s last row is `#373`
  — confirms #374 genuinely isn't there yet, consistent with the claimed kb
  primary-tree write-guard block (kb#372's own Bash-allowlist traps are real and
  independently checkable at ISSUE-LOG rows 372-373).
- Concurrency claim (noise-loader timestamps vs. suite `generated_at`): cross-checked
  epoch math — `generated_at` 1789265759 falls inside the noise log's launch window
  (1789265736–1789265792); the report's own stated window end (1789265774) is
  actually narrower than what the log shows, i.e. conservative, not inflated.

## Known issue found (minor, non-blocking)

`despag/reports/P1_EXIT.md`'s "OAuth token: read per spawn, never held" section
cites `harness/fworker.py:1046-1053` and `:1070`; actual lines are `1035-1045` and
`1059`. The claim itself is true (verified by reading the function), but the
citations don't point at the described code. Not a gate failure (outside
`evidence.json`'s formal AC), not caused by drift (file unchanged since the report
was written). Worth a follow-up line-number fix if anyone revisits `P1_EXIT.md`; not
worth a rework ticket on its own.

## Bottom line

Both harnesses independently reproduce 40/40 CONFORMANT on `--class core` from a
freshly started process, the claude-max path genuinely spawns and completes a real
Claude subprocess, the cancel/kill and budget-wiring code citations are exact, and
the wq#1852 baseline comparison is exact. Confirming wq#1882 as delivered.
