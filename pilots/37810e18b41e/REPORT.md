# PILOT 37810e18b41e — Report

Runner: wq#3408, worktree wq3408-a1. Attempted 2026-10-02.

## Outcome: baseline does not reproduce (kill criterion hit)

Per CARD.md "Kill criteria": *"Baseline does not reproduce -> stop, report the
baseline defect. Do not proceed."* That condition was hit before any query was run:

1. **No path to the run node.** The card pins `Node: hathor`, but this worker is
   sandboxed to a fixed worktree plus a short `--add-dir` list
   (`/home/lumen/agents/work-queue`, `/home/lumen/mailbot`, `/home/lumen/pokemon`,
   `/home/lumen/bin`, `/home/lumen/foreman`, `/home/lumen/kb`, `/tmp/fworker-wq3408`).
   `ssh` is not on the per-command allowlist (`harness/worker-settings.json` has no
   `ssh` entry), so there is no way to execute anything on hathor from this session.
2. **Fixed sample is unreachable.** `/home/ubuntu/lumen/pilot/comed_sample.jsonl`
   (the card's "Fixed sample" path) is outside every allowed directory; a direct
   `ls`/`cat` on it is refused by the sandbox ("may only list files in the allowed
   working directories").
3. **The baseline command does not exist in this repo.** The card's baseline/treatment
   commands both invoke `python -m lumen.pilot.run`. There is no `lumen` package, no
   `pilot/run.py`, and no `COMED` reference anywhere in this checkout
   (`grep -rn "COMED" --include=*.py --include=*.md .` outside this pilot's own
   CARD.md/REPORT.md returns nothing; `find . -iname "comed*"` returns nothing).
   Prior pilots in this same repo (e.g. `pilots/67af7c7e9ecf/run.py`,
   `pilots/9a4a9dd52121/run_pilot.py`) each ship their own bespoke runner script —
   there is no shared `lumen.pilot.run` entry point to fall back to, and authoring
   one from scratch would be inventing the measurement plan, which
   `research/DEFER_PILOT_LANE.md` §3.1a (cited in CARD.md) reserves for the card's
   generator, not the runner.

No baseline queries were executed; `baseline.jsonl` and `treatment.jsonl` were never
produced. escalation_rescue_delta_pp is therefore **not measured** (0 of 200 queries
run on either arm).

## Why this isn't a research-prompt rescue

The open question (does COMED-style escalation beat XAGREE on Lumen's own traffic)
is still unanswered and still requires the real run described in the card. This
report does not attempt to answer it from memory or by building a different test —
doing so would contradict the card's own instruction that the runner blocks rather
than inventing a measurement plan when the spec can't be executed as written.

## What would unblock a re-run

- Grant the dispatched worker either `ssh`-to-hathor on the per-command allowlist,
  or re-home the pilot's working paths under an `--add-dir` root the worker already
  has.
- Confirm whether `lumen.pilot.run` is expected to already exist on hathor (outside
  this repo checkout) or whether the card's generator owes a runner script, per
  `research/DEFER_PILOT_LANE.md` §3.1a.

RECOMMENDATION: SKIP
Measured numbers: none — baseline unreproducible (0/200 queries run on either arm); escalation_rescue_delta_pp not measured.
