# wq#2306 — verify-pass (archetype 8) of wq#2246 (PR #803, `186897f`)

Verifier: claude-sonnet-5@high, fresh instance (same model as author, claude-sonnet-5).
Verdict: **CONTRADICTED** — one of the offline tests the PR's done-notes claim pass
actually fails, deterministically, from a real defect in the harness's own test
scoring, not a flaky/environmental artefact. Escalated as **wq#2651**
(opus-5@high judgment, `deps=2246`).

## What wq#2246 shipped

`tools/macbench/`: matrix runner (`macbench.py`, `mb_matrix.py`, `mb_engines.py`,
`mb_measure.py`, `mb_node.py`, `mb_runner.py`, `mb_render.py`) + an 8-task agentic
suite (`mb_agentic.py`, `agentic/suite/*`) + 42 offline tests. Done-notes claim: "42
offline tests pass, dry-run plan works. Agentic baseline on hathor-llama8b via
gateway: 0/8. NOT DONE: live 2-row speed run on a Mac."

## Confirmed correct

- **Safety guards, live-probed.** `Node.guard_identity()` refuses `thor` both by
  name and by host IP (100.112.151.100) — confirmed live:
  `Node('thor', ...).guard_identity()` and `Node('wadjet', {'host': THOR_IP})` both
  raise `Refused("thor is never benched")`.
- **The command deny-list distinguishes read vs. write systemctl verbs correctly** —
  live: `systemctl status/list-units/is-active` all pass `screen()`; the actual
  power-action verbs (reboot/shutdown/`dd if=`/etc.) are blocked even earlier, by
  this sandbox's own `guard.sh`/`action_gate.py`, before macbench's own regex is
  ever reached — independent confirmation of the same intent at a different layer.
- **Disk, serving-group, THOR, restore-prior-resident, and deny-list behavior are
  all covered by the test suite** (`test_thor_never`,
  `test_serving_group_node_refused_without_flag`,
  `test_all_node_commands_pass_the_deny_list`,
  `test_restores_prior_resident_and_unloads_ours`,
  `test_missing_model_is_pulled_after_disk_check`) and all of those pass.
- **The agentic baseline's 0/8 score against hathor-llama3.1-8b is plausible, not
  a smoking gun on its own** — llama3.1-8b is known-weak at structured tool-calling
  through a gateway; several tasks show real tool_calls/tool_errors (e1_rename: 9
  calls/4 errors), consistent with a genuinely struggling small model rather than
  an obviously broken harness. Not confirmed as *correct*, just not disprovable
  from the committed evidence (raw JSONL with `test_tail`/`is_error` wasn't
  committed, only the rendered `.md` table) — flagged as a gap below, not a defect.

## Contradiction found — the offline test suite's own proof is broken

Ran the actual probe the PR claims passes:

```
$ python3 -m pytest tools/macbench/tests -q
..F.......................................                               [100%]
FAILED tests/test_agentic.py::test_task_fails_on_baseline_and_passes_with_reference_solution[e2_default]
1 failed, 41 passed in 3.15s
```

Not 42/42. Reproduced twice in isolation (`-q tests/test_agentic.py::...[e2_default]`
alone), fully deterministic — not order-dependent, not flaky.

**Root cause.** `test_task_fails_on_baseline_and_passes_with_reference_solution`
(`tests/test_agentic.py:22`) runs `run_test()` on the task's *baseline* repo
(asserts it fails), then `shutil.copytree(solution, repo, dirs_exist_ok=True)`
over the **same directory** and runs `run_test()` again (asserts it now passes).
Neither `mb_agentic.build_repo()` nor `run_test()` clears `__pycache__` or
disables bytecode caching between the two `subprocess.run(test_cmd, shell=True,
cwd=repo, ...)` calls. `e2_default`'s baseline and solution `pkg/config.py` are
byte-identical in size and checkout mtime:

```
$ stat -c '%Y %s %n' agentic/suite/e2_default/repo/pkg/config.py \
                     agentic/suite/e2_default/solution/pkg/config.py
1789943147 23 .../repo/pkg/config.py
1789943147 23 .../solution/pkg/config.py
```

CPython's legacy `.pyc` invalidation compares only source mtime+size, so the
second `python3 -m unittest discover` subprocess silently reuses the bytecode
compiled from the **baseline** `config.py` (`DEFAULT_TIMEOUT_S = 30`) instead of
reading the copied-in solution's `= 45`:

```
AssertionError: 30 != 45
```

Manually confirmed the solution files themselves are correct —
`solution/pkg/config.py` reads `45`, `solution/pkg/client.py`'s `get()` defaults
to `45` — the bug is in the test harness's cache handling, not the task fixture.

## Why this is not confined to the offline test

Every task prompt explicitly invites the model under test to self-check mid-session:
`e2_default`'s prompt ends "Check your work with
`python3 -m unittest discover -s tests -q`" (same phrasing pattern across the
suite). In a live `run_task()`, `build_repo()` does start from a fresh `rmtree`
each *task*, so a first-ever compile isn't contaminated by a *different* task's
run — but nothing stops the model from running the test command itself one or
more times mid-session (as instructed) before its final edit. If a later edit in
the same session happens to leave a touched file's mtime+size unchanged from an
earlier self-check compile (same second, same byte count — not a rare
coincidence for a single-line numeric or string-length-preserving fix, which
several of these 4 mechanical-edit tasks are by construction), the final scoring
`run_test()` call can replay a **stale pass or fail** into the number the harness
treats as ground truth for the model's agentic score. The scoring path
(`run_task` → `run_test` → `diff_stats`) has no defense against this at all: no
`PYTHONDONTWRITEBYTECODE`, no `sys.dont_write_bytecode`, no `__pycache__` purge.

## What I tried to break, that held up

- **`test_editing_the_test_does_not_pass_a_task`**: confirmed live that a model
  overwriting the protected test file cannot cheat — `run_test()` restores the
  protected file from the suite before scoring, and the test still fails.
- **`diff_stats` excluding protected files from the diff count**: read and
  reasoned through `mb_agentic.diff_stats()` — correctly filters `protected`
  paths so a model can't inflate `diff_lines`/pass `files > 0` by touching only
  the test file.
- **Sweep resolution (`mb_matrix.resolve_sweep`) picking a winner with no
  `variants` for a requested quant**: correctly emits a `_Skipped` with reason,
  does not crash or silently drop the row — covered by passing tests.
- **SIGTERM mid-row**: `Runner.run()`'s SIGTERM→`KeyboardInterrupt` handler is
  structurally sound — `KeyboardInterrupt` doesn't inherit `Exception`, so it
  correctly bypasses the per-row `except Exception` catch and reaches the outer
  `finally` that restores prior residents. Not live-tested (would need a real
  node), but the control-flow reasoning is unambiguous from source.

## Gap noted, not blocking

The committed baseline result (`results/baseline-hathor-llama8b.md`) only has the
rendered markdown — the raw JSONL with `test_tail`/`is_error`/`result_subtype`
per task wasn't committed (presumably `.gitignore`d), so the 0/8 score can't be
independently root-caused from the repo alone. Worth keeping the raw JSONL
alongside future baseline tables.

## Disposition

Per doctrine ("do not re-implement; verify"), no fix applied. `wq review 2246
reject` was attempted first and refused (`wq#2246 is 'done' — review only acts on
pending_review tickets`) — recorded the verdict here instead, per the wq#2350/wq#2298
precedent. Filed **wq#2651** (lane build, pri 1, `model=claude-opus-5`,
`effort=high`, `deps=2246`, archetype `rca-investigation`) with the exact repro and
a concrete fix direction (set `PYTHONDONTWRITEBYTECODE=1` in the `run_test()`
subprocess env, or `shutil.rmtree` every `__pycache__` under `repo` before each
`run_test()` call — both are one-line fixes). `kb/ISSUE-LOG.md` row 744 records
this finding.
