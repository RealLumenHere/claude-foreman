# Promoter tickets cite an excluded read root (wq#1535)

Design doc, written before code. 2026-09-08, work-1535.

## 0. What is true (verified on the box, not restated from the ticket)

- `minerva_promote.py` (untracked in the minerva repo, wired from `minerva_factory._promote_parked`
  which is also an uncommitted local edit — wq#1500 never landed) turns YELLOW/RED factory
  plans into `fleet` lane tickets. 14 open ones: wq#1502-1515, 1520.
- `capability_lint.derive` scans **title+detail in full**. There is no section awareness; a
  "How to close" section is scanned exactly like the body. The ticket's option (b)-first-half
  ("move it to the section the lint does not scan") does not exist as written.
- Every one of the 14 carries the same two class strings (`capability_lint.py --ticket N --json`):
  1. `dir(/home/lumen/minerva/proposals/pending) is not under allow.read_dirs` — from the
     `proposal md:` line.
  2. `dir(/home/lumen/minerva) is not under allow.read_dirs` — from the "How to close" line
     `sqlite3 /home/lumen/minerva/minerva.db "UPDATE proposals SET status='deployed' ..."`.
     That line is itself a doctrine violation: `minerva_factory.py:645` says deployed is never a
     raw flag-flip (wq#675) and `verify-all` reclassifies raw flips to `applied_unverified`.
- YELLOW tickets (9 of 14) add a third: `cited path /home/lumen/instructions/approvals/<plan>.approved
  does not exist (not guessed)`. Two further bugs hide in that same line: the factory's approval
  file is `F<plan_id>.approved` (`minerva_factory.py:774,831,919`) and `apply` needs `--approved`
  as well as the file (`:775`). A worker following the ticket literally touches the wrong file and
  then runs an apply that refuses.
- Per-ticket extras (`/home/lumen/agent/...bak.PREAPPLY`, `/home/lumen/bin/...`, `restart(lumen-bench@lumen)`)
  come from `apply_cmd`/`rollback_cmd`/`targets` being inlined into the body. They describe what
  the FACTORY does at apply time (as uid lumen, with its own reach), not what the reviewing
  worker needs.
- `/home/lumen/minerva` is deliberately excluded (policy comment, wq#1467: credentials in
  `context/sections`). `stage_yellow` already writes the YELLOW plan request to
  `/home/lumen/instructions/approvals/F<plan>.md` — an allow root — so half the plan data is
  already worker-readable; the ticket just does not cite it.
- `capability.preflight` (`path_problems`) only requires a cited path to be UNDER a reachable root;
  it does not require existence. So a lint tag on the parent directory is enough for a to-be-created file.

## 1. Class

**A ticket generator inlines paths from its OWN reach (the factory's) into a body that a
DIFFERENT principal (a fleet worker) is linted against.** Three sub-shapes, each fixed once:

| shape | instance here | class fix |
|---|---|---|
| evidence lives in an excluded root | `minerva/proposals/pending/*.md` | generator publishes evidence into a granted root and cites that |
| closing instruction embeds a privileged path | `sqlite3 /home/lumen/minerva/minerva.db ...` | close via a factory verb (no path, and no raw flag-flip) |
| precondition-vs-closure artifact | `approvals/F<plan>.approved` cited before it exists | lint learns the closure-artifact shape; its reach is the parent dir |

Where else the class exists: `stage_yellow`'s own `F<plan>.md` and `INSTALL.md` cite
`/home/lumen/minerva/minerva_factory.py` (excluded root) as the apply command — same shape, but
those files are read by humans/orchestrators, not linted; they get the shared helper anyway so
the two never disagree again.

## 2. Options

**A. Add `/home/lumen/minerva/proposals` to allow.read_dirs.** Rejected: the exclusion of
`/home/lumen/minerva` is deliberate and the sweep semantics are per allow-root; a root inside an
excluded tree is a precedent that invites `/home/lumen/minerva/<something-else>` next.

**B. Nest under an existing root: `/home/lumen/instructions/proposals/<pid>/`.** Works with zero
policy change (root already swept, 190 files, 0 hits). Rejected on isolation: a future secret-shaped
token in an LLM-generated proposal would land a sweep hit inside the root that holds every playbook
and approval; `sweep_hit_under` scopes the refusal to the sub-dir, but the `instructions` root row
itself turns red in the report and on the dashboard.

**C. Dedicated root `/home/lumen/proposals/<pid>/{proposal.md,plan.md}`** (chosen). One policy row,
swept in the PR, isolated blast radius, and the promoter masks with the sweep's own PATTERNS
before it writes — so the root is clean by construction, not by luck, and a masked token can
never re-match (mask keeps 4 chars; every pattern needs 20+).

**Approval path — lint side.** Options: (i) skip a "How to close" section — the lint has no
sections and adding them means every generator must emit the marker; (ii) walk any non-existent
path up to an existing ancestor — the doctrine (`normalise_dir` docstring, §2.1) refuses that as a
guess; (iii) an explicit **closure-artifact glob** hard-coded next to the deny list:
`/home/lumen/instructions/approvals/*.approved` resolves to its parent when the parent exists.
(iii) chosen: narrow, deterministic, same place as the other hard-coded shape rules, and the
promoter additionally emits the correct `F`-prefixed path via the factory's own helper.

**Plan detail in the body.** The body keeps a one-line plan summary (id/lane/change_type/status/
lane_reasons) and cites `plan.md`. `targets/apply_cmd/health_cmd/rollback_cmd/draft_diff` move to
`plan.md`. A worker needing a restart still hits the broker's fail-closed gate at call time;
nothing is widened.

## 3. Chosen design

minerva repo (`minerva_promote.py`, `minerva_factory.py`):
- `minerva_factory.approval_path(plan_id)` + `approval_cmds(plan_id)` — single source for the
  file name and the two-step approve/apply; `stage_yellow`, `INSTALL.md`, `install_staged_service`,
  `apply_plan` and the promoter all use it.
- `minerva-factory close <proposal_id> (--applied | --reject "why")` — `--applied` sets
  `applied_unverified` then runs `verify_proposal` (deployed only on a passing health check);
  `--reject` sets `reject` + `decided_at` + `decision_reason`. The ticket body cites this verb,
  never a db path.
- `minerva_promote.publish(prop, plan)` writes `/home/lumen/proposals/<pid>/proposal.md` (copy of
  the pending md) and `plan.md` (full plan row), each run through `read_dir_sweep.PATTERNS` +
  basename globs with matches masked and counted in the file header. Fail closed: if the sweep
  module cannot be imported nothing is published and the ticket says so.
- `_detail` cites the published paths only; `How to close` = `approval_cmds` (YELLOW) or the
  manual-change sentence (RED), then `minerva-factory close ...`.
- `minerva_promote.py reemit <tid>...` rewrites an existing promoter ticket's detail in place via
  `wq edit --detail` (pri/lane/notes untouched), so the 14 live tickets get the new body without
  a re-add (which the title-dedup would refuse anyway).
- The wq#1500 `_promote_parked` hook (currently an uncommitted primary edit) lands in the same PR.

foreman repo:
- `capability_lint.CLOSURE_ARTIFACT_GLOBS` + resolution in `_derive_dirs` (before the
  not-guessed check, after the deny check on the cited path).
- `fleet-exec-policy.yaml allow.read_dirs += /home/lumen/proposals` with sweep evidence.
- `BROKER_GRANT_COVERAGE.md §3` gets the new root row.
- tests in `harness/tests/test_capability_lint.py`.

## 4. Test plan (test to failure)

1. Unit: closure glob resolves to parent → `dir(parent)` tag; parent missing → still
   `unresolved` (not guessed); an unrelated non-existent path → unchanged `unresolved`; a
   closure-shaped path under a deny prefix → `refused` (deny still wins first).
2. Unit: promoter `_detail` on a synthetic YELLOW plan cites only `/home/lumen/proposals/<pid>/`
   and `approvals/F<plan>.approved`; `publish` masks a planted `sk-...` token and reports the count;
   `publish` with the sweep import broken publishes nothing.
3. `read_dir_sweep.py /home/lumen/proposals` after publishing all 14 → 0 hits (cited in PR).
4. `capability_lint.py --ticket N --json` for each of the 14 after reemit: the three class
   strings are gone. Expected residue: 1503/1506 still cite `/home/lumen/agent` in `current_state`
   prose (the proposal text itself); that is a real, correct refusal of a deliberately excluded root.
5. `foreman_dispatcher.py run --once --dry-run`: 1504/1507/1508/1514 reach `would_spawn` (or the
   next non-capability decision). Nothing is executed.
6. Failure probe: reemit one ticket with the sweep runtime deleted → lint reports
   `read_dir_sweep is missing` (fail closed) → restore runtime → clean.

## 5. Rollback

- foreman: revert the PR (lint glob + policy row); `FOREMAN_NEEDS_LINT_MODE=warn` is the
  standing kill switch for the whole stage.
- minerva: `MINERVA_PROMOTE=0` stops new tickets; `/home/lumen/proposals` is a plain copy, safe to
  delete; the 14 tickets' old bodies are in `minerva_promote.py reemit --backup` output under
  `/home/lumen/proposals/_reemit_backup/<tid>.txt` for `wq edit --detail` restore.

## 6. Results (2026-09-08, before landing)

- Unit: foreman 138 passed (capability_lint 88 incl. 5 new, capability_allow, read_dir_sweep,
  capability_replay); minerva tests/test_minerva_promote.py 5 passed.
- `read_dir_sweep.py /home/lumen/proposals` after `minerva_promote.py reemit` of all 15 promoter
  tickets: files=45 hits=0.
- Lint per ticket (worktree policy + fresh runtime): 13/15 clean with `dir(/home/lumen/proposals/<pid>)`
  (+ `dir(/home/lumen/instructions/approvals)` for the 9 YELLOW). Residue, all in the proposal's own
  prose, not the class: 1502 and 1520 name a script the prototype would create
  (`minerva_fuse_probe.py`, `scratch/split_prefill_test.py`) — ordinary not-guessed; 1515 mentions
  `/home/lumen/bin` (cleared by the fleet lane's base add_dirs in the dispatcher).
- Failure probe: sweep runtime removed → 1507 `unresolved=2` (`read_dir_sweep is missing`); restored → clean.
- Dry-run dispatcher (`run --once --dry-run`, worktree env): `dispatchable: 14` vs 1 on the live pass
  minutes earlier; 1504/1507/1508/1514 all reach `capability_applied` and fall through to the
  next stage (`preflight no_fresh_stamp`, a worktree artefact — the primary has the stamp).
- The live 15:08Z pass, run by the timer mid-work with the old primary policy, already shows the
  class strings gone and only the pending policy row left: `dir(/home/lumen/proposals/...) is not
  under allow.read_dirs`.
