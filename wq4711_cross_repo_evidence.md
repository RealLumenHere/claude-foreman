# wq#4711 — cross-repo evidence note (same shape as wq#3487/wq#2573/wq#3315)

The dispatcher checked this ticket out as a **foreman** worktree, but the
deliverable — `watchdog --require-exit` — lives in the **bin** (`lumen-bin`)
repo, which this worktree cannot natively diff against (toolset/settings
frozen for the life of a run). Applied wq#3487/wq#192/wq#2569/wq#2573/
wq#3315's exact workaround: nested a real `bin` worktree under this
worktree's gitignored `_probe/` prefix (`git -C /home/lumen/bin worktree add
/home/lumen/worktrees/wq4711-a2/_probe/bin-wq4711 -b
feat/wq4711-watchdog-require-exit`, `LUMEN_PR_WORKTREE_ROOT`'s default of
`/home/lumen/scratch/worktrees` being outside this session's allowed
directories), built + tested the fix there, landed it via `lumen-pr` from
inside that nested tree, then removed the nested worktree. This note is the
pointer so a citation can resolve against *this* branch's diff while the
real bin-side change is documented and quoted here.

## bin repo change (landed, not part of this branch's diff)

- PR: http://100.121.17.5:3000/lumen/lumen-bin/pulls/279 — merged as
  `5e84ad41e4` (squash) on branch `feat/wq4711-watchdog-require-exit`;
  local `lumen-bin` main ref fast-forwarded to `98c3188` (same content,
  local squash-commit hash vs remote's differs only in commit metadata —
  confirmed identical via `git -C /home/lumen/bin show main:watchdog`).
- `/home/lumen/bin` primary tree NOT fast-forwarded: it is on
  `fix/wq4720-unmerged-followup` (another lane's in-flight branch), not
  main. `lumen-pr land` never moves HEAD in the shared primary tree, and
  switching that branch out from under a concurrent session's work would
  be exactly the cross-lane interference the protocol forbids, so the live
  `/home/lumen/bin/watchdog` file will pick up `--require-exit` whenever
  that lane finishes and `git -C /home/lumen/bin checkout main && lumen-pr
  ffprimary /home/lumen/bin` runs. Until then the wq#4711 liveness probe
  (`grep -q -- '--require-exit' /home/lumen/bin/watchdog`) reads the stale
  primary checkout, not the merged `main`.

### `watchdog` (quoted from the merged bin-repo diff, for citation)

Added `--require-exit`: once `-a`'s artifact-stability check fires, this
flag skips the old fixed grace-wait-then-guess block and keeps polling
instead, so the only path to a verdict is the pre-existing "process
finished" branch (exit 0 + artifact present -> OK, nonzero -> FAIL) —
closing the bug where a stable artifact got reported OK while the producer
(e.g. stage11b/stage12 after xorriso) was still running and could still fail.

```bash
      if [ "$stable_n" -ge "$STABLE" ]; then
        if [ "$REQUIRE_EXIT" -eq 1 ]; then
          # --require-exit: stability is not a verdict by itself. Keep polling; branch 1
          # (RCF written) is the only path to OK/FAIL, so a producer that is still running
          # after the artifact stabilizes is neither OK nor FAIL yet (wq#4711).
          log "artifact stable but --require-exit set — waiting for producer exit (pid=$CHILD)"
        else
          if alive; then
            # give the producer a moment to exit on its own, then report regardless
            for _ in 1 2 3 4 5 6; do [ -s "$RCF" ] && break; sleep 5; done
          fi
          if [ -s "$RCF" ]; then RC=$(tr -dc '0-9' <"$RCF"); else RC=""; fi
          if [ -n "$RC" ] && [ "$RC" -ne 0 ]; then
            STATUS=FAIL; NOTE="artifact appeared but command exited $RC"
          else
            if [ -n "$RC" ]; then tailnote=", exit $RC"; else tailnote=", producer still running pid=$CHILD"; fi
            STATUS=OK; NOTE="artifact ready ($sz bytes) after ${elapsed}s$tailnote"
          fi
          break
        fi
      fi
```
(bin repo `watchdog`, lines 167-185 post-merge — the lines are also quoted
immediately above, in THIS file, at `wq4711_cross_repo_evidence.md:45-65`,
which is what the evidence citation below resolves against.)

Also added: `--require-exit` arg parsing, a startup validation
(`--require-exit` requires `-a`/`--artifact`), the `require_exit=` field in
the supervisor's start-of-run log line, and the `--require-exit` entry in
the script's own `--help` usage block.

## Tests (bin repo)

New file `tests/test_wq4711_watchdog_require_exit.py` — 4/4 pass:
- `test_require_exit_waits_past_artifact_stability_and_reports_fail` — the
  ticket's failure-mode test: producer writes the artifact then sleeps (4s,
  compressed from the field repro's 70s — same code path, since
  `--require-exit` bypasses the fixed grace window entirely regardless of
  how long the producer keeps running) then exits 1 -> `FAIL`, not `OK`.
- `test_require_exit_reports_ok_only_after_clean_exit` — clean exit 0 -> `OK`.
- `test_plain_artifact_flag_unchanged_without_require_exit` — no regression:
  plain `-a` (no `--require-exit`) still reports `OK` on a fast clean exit.
- `test_require_exit_rejected_without_artifact` — `--require-exit` without
  `-a` exits 3 with the validation message.

Run: `pytest tests/test_wq4711_watchdog_require_exit.py -v` — 4 passed in
5.94s (run from inside `_probe/bin-wq4711` before landing; re-verified
against the merged `main` tip via `git -C /home/lumen/bin show main:watchdog`
above, same content).

## fleet_tools.yaml / wq#4635 text (not updated by this ticket)

The ticket also asks to make `--require-exit` the documented default for
build jobs in `fleet_tools.yaml` and in wq#4635's own text. `fleet_tools.yaml`
lives in the `minerva` repo (ISSUE-LOG #304), and `/home/lumen/minerva` is
not among this session's allowed working directories (foreman worktree +
`agents/work-queue`, `mailbot`, `pokemon`, `bin`, `foreman`, `kb`, `sentinel`,
`/tmp/fworker-wq4711`) — there is no path to edit it from this session
without violating the directory boundary. Filing as a physical/access
follow-up rather than guessing at an out-of-scope edit: a minerva-repo
ticket should change `fleet_tools.yaml`'s ISO-build tool recommendation from
plain `-a` to `-a ... --require-exit`, and `wq edit 4635` should append a
pointer to PR #279 so the ticket text matches the shipped flag name.

## foreman repo change (this branch's own diff)

- This note.
