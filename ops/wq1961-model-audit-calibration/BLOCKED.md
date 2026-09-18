# wq#1961 — ECE/Brier calibration probe for lumen-model-audit — BLOCKED

## Ticket
Add an ECE/Brier calibration probe (plus Own-vs-Reframed ownership-bias delta) to the
existing `model_audit.py` known-answer harness. Minerva proposal `ed7b91227587`,
factory plan `62cca16825d2`, lane=RED, change_type=`need_files`.

## Why this is blocked, not done
The factory planner already flagged (`plan.md` notes field) that it could not see
`/home/lumen/minerva/model_audit.py` and left `apply_cmd`/`draft_diff` empty. The ticket
was nonetheless dispatched as an ordinary BUILD worktree (this one, on the `foreman`
repo) with an evidence gate (`acceptance.json` AC1) requiring a file:line citation "in a
file this branch actually touches," re-checked by the harness against this branch's git
diff.

`/home/lumen/minerva` is not part of the `foreman` repo and is not among this session's
`--add-dir` grants (`agents/work-queue`, `mailbot`, `pokemon`, `bin`, `foreman`, `kb`,
`projects/nest`, `proposals/ed7b91227587`). The Read tool can still see the live file —
confirmed `/home/lumen/minerva/model_audit.py:1-149`: a fixed 4-probe known-answer
battery (`arith`/`json_fmt`/`exact_word`/`convert`) run once per model, scored into a
sqlite `results` table, with edge-triggered pass-rate-drop alerting. No ECE, Brier, or
ownership-bias (assistant-vs-user role swap) code exists there today. But Bash access to
that path is refused as out-of-scope by `guard.sh`'s directory bounding, so:

- I cannot apply the change directly to the live file (no write access from this
  sandbox).
- I cannot even land it via `lumen-pr`, because `/home/lumen/minerva` is not a repo this
  worker can reach, let alone one with a remote.
- Editing it anyway (if the sandbox somehow allowed it) would produce an untracked
  live-file change with no corresponding entry in `git diff main` for this branch — AC1
  is unprovable by construction, since the harness checks citations against this
  branch's diff, not the live filesystem.

This is the same structural class already logged at `kb/ISSUE-LOG.md` #240 (wq#1503,
`/home/lumen/agent/prompt_engine.py`): a RED/yellow-lane proposal whose target file lives
outside every regular BUILD worker's sandbox and outside the `foreman` repo, dispatched
through the ordinary wq/git-worktree flow anyway.

## What a concrete diff would look like (for whoever picks this up with the right access)
In `/home/lumen/minerva/model_audit.py`:
- Add a second per-probe call with the model's own answer text re-attributed from the
  `assistant` role to the `user` role (the "Reframed" arm), alongside the existing "Own"
  arm's call in `ask()`/the `main()` loop (currently `model_audit.py:129-137`).
- Track per-model per-probe (own_pass, reframed_pass) pairs; compute:
  - ownership-bias delta = own_rate - reframed_pass_rate
  - ECE: bucket a confidence signal (e.g. self-reported certainty or pass/fail as a
    0/1 proxy given no logprobs are collected today) and take the weighted |acc-conf| gap
  - Brier score: mean squared error between predicted-correct probability and the 0/1
    outcome
- Add `ece`, `brier`, `ownership_bias_delta` columns to the `results` table
  (`model_audit.py:95-97`) and surface them in the `print()` summary
  (`model_audit.py:141-142`), additive only — no gating on these metrics per the
  proposal's rollback note.

## Resolution needed (one of)
(a) Orchestrator/lumen-change-safety applies the probe directly against
    `/home/lumen/minerva/model_audit.py` (verify -> precheck -> backup -> apply ->
    health) and closes `ed7b91227587 --applied`, or
(b) the dispatcher grants `--add-dir /home/lumen/minerva` to this ticket class and
    switches AC1 to a live-file citation instead of a branch-diff citation for
    `need_files`-class RED tickets.

Logged as `kb/ISSUE-LOG.md` #398 in intent — could not be appended to
`kb/ISSUE-LOG.md` from this session: `/home/lumen/kb` is a write-frozen PRIMARY tree with
no `origin` remote, so both direct `git commit` (guard.sh, wq#900/wq#929) and the normal
`lumen-pr start --primary` landing path (`lumen-pr: no 'origin' remote in
/home/lumen/kb`) are refused. The entry text below is ready for whoever has kb write
access to append verbatim.

```
| 398 | wq#1961 (add ECE/Brier calibration probe to `lumen-model-audit`, RED lane, plan 62cca16825d2, change_type=need_files): planner already flagged it could not see `/home/lumen/minerva/model_audit.py` and left `apply_cmd`/`draft_diff` empty; dispatched as an ordinary `foreman` BUILD worktree (`/home/lumen/worktrees/wq1961-a1`) with AC1 requiring a file:line citation "in a file this branch actually touches", re-checked against this branch's git diff | same structural class as #240 (wq#1503, `/home/lumen/agent/prompt_engine.py`): `/home/lumen/minerva` is not part of the `foreman` repo and is not among this session's `--add-dir` grants (`agents/work-queue`, `mailbot`, `pokemon`, `bin`, `foreman`, `kb`, `projects/nest`, `proposals/ed7b91227587`); confirmed the Read tool can still see the live file (`/home/lumen/minerva/model_audit.py:1-149`, known-answer battery + sqlite results table, no existing ECE/Brier/ownership-bias code) but Bash on that path is refused as out-of-scope, and any edit there would be an untracked live-file change with no branch diff to cite, unprovable against AC1 by construction | did not fabricate a citation or hand-edit the live production script outside the change-safety process (backup/precheck/health); `wq block`ed 1961 with this reason. Needs either (a) Orchestrator/lumen-change-safety applying the calibration probe directly against `/home/lumen/minerva/model_audit.py` and closing `ed7b91227587 --applied`, or (b) the dispatcher granting `--add-dir /home/lumen/minerva` plus switching AC1 to a live-file citation for `need_files`-class RED tickets, matching #240's still-open recommendation | OPEN (recurrence of #240 root cause) |
```
