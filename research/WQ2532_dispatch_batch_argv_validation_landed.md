# wq#2532 — dispatch-batch argv validation: already landed (dupe of wq#2526)

Ticket asked to land the eval-over-unvalidated-argv fix (`dispatch-batch --help`
reaching `default_spawn()`'s `eval`) into the `lumen-bin` repo and add a regression
test, per the FIXED-IN-PLACE note left against `/home/lumen/bin/dispatch-batch`
(backup `dispatch-batch.bak.20260924_234206_pre_argvalidate`).

## Finding: already on `origin/main`

`lumen-bin` `main` and `origin/main` both sit at `b18f1853cd46142cd43cf7e43d3cfdb4db7925ea`
("fix(dispatch-batch): validate ticket ids before spawn", `Refs: wq#2526`) — a sibling
ticket describing the identical defect class, landed before this dispatch started. No
new commit was needed; verified independently rather than taken on the commit message's
word:

```
$ git -C /home/lumen/bin log -1 -- dispatch-batch
commit b18f1853cd46142cd43cf7e43d3cfdb4db7925ea
    fix(dispatch-batch): validate ticket ids before spawn
    Refs: wq#2526
$ git -C /home/lumen/bin rev-parse main origin/main
b18f1853cd46142cd43cf7e43d3cfdb4db7925ea
b18f1853cd46142cd43cf7e43d3cfdb4db7925ea
```

## What the landed commit does

`/home/lumen/bin/dispatch-batch`:
- `:28` — `usage()` takes an exit code (`exit "${1:-64}"`) instead of always 64.
- `:29` — `case "${1:-}" in -h|--help) usage 0 ;; esac` — `--help`/`-h` print usage
  and exit 0 before the id loop runs, so `--help` never reaches `default_spawn`.
- `:34-36` — every remaining arg is checked against `''|*[!0-9]*`; a non-numeric or
  empty id is refused with `dispatch-batch: refusing non-numeric ticket id: '<id>'`
  and `usage 64`, in the parent, before anything detaches.
- `:45-56` — belt-and-suspenders inside `default_spawn()` itself: `router.py
  dispatch-cmd`'s stdout is only `eval`'d if its last line starts with `lumen-run `;
  anything else (a future flag-shaped id argparse itself turns into an rc0
  usage/help dump) is refused with `dispatch-batch: refusing to eval non-lumen-run
  output: <line>` instead of reaching `eval`.

Same commit adds `tests/test_dispatch_batch.py::test_help_flag_prints_usage_and_exits_0_without_detaching`,
`::test_non_numeric_arg_rejected_before_detach`, and
`::test_default_spawn_refuses_to_eval_non_lumen_run_output`, each asserting no
`runs/` dir gets created on refusal. Reran the full file independently:

```
$ cd /home/lumen/bin && python3 -m pytest tests/test_dispatch_batch.py -q
.......                                                                  [100%]
7 passed in 17.25s
```

## Why nothing lands via this ticket's own branch

`lumen-bin` is a separate repo from `foreman` (this worktree tracks `foreman`); the
fix and its test are already merged and pushed there under wq#2526, so there is no
further `lumen-bin` diff for wq#2532 to contribute — re-applying the same guard would
either no-op against an identical tree or conflict with itself. This note is wq#2532's
landing record inside `foreman`, the same "record as landed" pattern used for
`research/TOKEN_ENGINE_DESIGN.md` section 8.1 (wq#2273, commit `0673475`).
