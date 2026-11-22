# session-handoff (wq#1294)

> **Superseded in part by wq#1630** (`ops/wq1630-handoff-merge/`): the tool no longer writes or
> commits `kb/SESSION-HANDOFF.md`; it renders a marked generated region at the top of
> `/home/lumen/HANDOFF.md` instead, and `claude_context.sh --core` re-renders it at boot.
> Blocked-by-class and open-FRANK sections were dropped (`wq list --status blocked` covers the
> first; frank gates are retired). Read the wq1630 README for the current behaviour.

Resuming a fleet orchestrator session today costs ~10 turns of re-deriving
state: querying the work queue for in-flight workers, scrolling landed PRs,
grepping for blocked tickets, checking for open FRANK decisions, and reading
the Day-0 detectors' last state one at a time. `session-handoff` collapses
all of that into a single generated file.

**Tool**: `/home/lumen/bin/session-handoff` (its own repo, committed directly
per that repo's convention — see its git log). Reads `queue.db` read-only and
the detector stamp/laststate files under `/home/lumen/logs/`; writes and
`git commit`s `/home/lumen/kb/SESSION-HANDOFF.md`.

**Sections**: in-flight workers (claimed tickets), last 10 landed PRs (done
tickets whose notes cite a PR url — `wq done`'s evidence gate, wq#759/wq#899,
already makes that note a checked verdict, not a bare link), blocked tickets
grouped by a leading `TAG:` in `block_reason`, open FRANK decisions
(`frank_gated`/`pending_review`), the four Day-0 detectors' last-run age and
state (model-check, wq-health, secret-check, log-watch), and a free-text
"what the orchestrator was in the middle of" log that accumulates across runs
via `--note` (capped at the last 20 entries) — this is the one section that
isn't rederived from live state each run.

**Usage**:
```
python3 /home/lumen/bin/session-handoff                  # regenerate + commit
python3 /home/lumen/bin/session-handoff --note "..."     # leave a handoff note, then regenerate
python3 /home/lumen/bin/session-handoff --no-commit       # write only, for testing
```

**Cron**: `session-handoff.timer` runs it every 30 minutes (plus boot+3min).
Install (root, via `lumen-run --root`):
```
lumen-run start --root -- bash /home/lumen/foreman/ops/wq1294-session-handoff/install.sh
```
Manual run: `systemctl start session-handoff.service`

## Timer is intended-off (wq#5251)

The 2026-10-08 subtraction disabled `session-handoff.timer` on purpose
(`/home/lumen/scratch/subtraction/timers-disable-phase2.txt`, line 54), and
wq#1630 already re-renders the handoff region at boot via
`claude_context.sh --core`. So `install.sh --verify` checks only the two unit
files. It does not check that the timer is enabled or active. Do not run
`install.sh` as root to "fix" the timer: it runs `systemctl enable --now` and
undoes the subtraction. Re-enabling the timer is a decision. If someone makes
it, they put `venabled`/`vactive`/`vtimer_scheduled` back in the verify block.
