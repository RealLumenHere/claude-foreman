# wq#4815 — root-apply wq3354-fleet-selfheal, install.sh --verify fix

Author: claude-sonnet-5 (build worker, worktree `wq4815-a2`).

## Before: live gap matched the ticket

`lumen-run start --name wq4815pre -- bash /home/lumen/foreman/ops/wq3354-fleet-selfheal/install.sh --verify`
(unit `lumen-job-wq4815pre-1793667371`), exit 1 — the exact three seed-key MISSes the
ticket quotes, plus `sh_providers.py differs` and (new since wq#4814/wq#4816/wq#4817
landed) two more seed keys not yet live: `nodes.ignore.wadjet`, `nodes.notes.sobek`,
`nodes.notes.bes`.

## Root cause: install.sh's own pytest gate fails when actually run as root

Privileged apply (`lumen-run start --root -- bash .../install.sh`) refused immediately:
`REFUSING: .../tests does not pass on this checkout`. Direct repro
(`lumen-run start --root -- python3 -m pytest -q .../tests`) showed why:
`ModuleNotFoundError: No module named 'ruamel'` — `selfheal_merge.py` imports
`ruamel.yaml`, which is only installed in lumen's `--user` site-packages
(`/home/lumen/.local/lib/python3.14/site-packages`). `systemd-run --root` (no `-u`) runs
with no `HOME`, so root's `python3` never sees that site-packages dir and the bundled
test suite can never pass as root — this install.sh could never complete a real
privileged apply, root-apply precedent to the contrary (wq#3584/wq#3585) predates the
`ruamel` dependency (wq#4445).

The fix mirrors a pattern already in the same file: the live config merge
(`install.sh:79-80`) already runs `selfheal_merge.py` via `runuser -u lumen --` for this
exact reason. The pytest gate (`install.sh:67`, old) did not, and was the only other
root-run Python invocation in the script that needs lumen's env (all of `CODE[]` besides
`selfheal_merge.py` is stdlib-only, confirmed by grepping every file's imports).

## Fix

`ops/wq3354-fleet-selfheal/install.sh:70` — wrapped the pytest gate in `runuser -u lumen --`,
same as the merge step below it.

## Install + verify (after merging main to pick up wq#4816/wq#4817's seed keys first)

- `lumen-run start --root --name wq4815install3 -- bash
  /home/lumen/worktrees/wq4815-a2/ops/wq3354-fleet-selfheal/install.sh` — exit 0, merged
  `nodes.notes.sobek`/`nodes.notes.bes` into the live `selfheal.yaml`, re-enabled all
  three timers/services.
- `lumen-run start --name wq4815verify3 -- bash
  /home/lumen/foreman/ops/wq3354-fleet-selfheal/install.sh --verify` — `VERIFY OK`, exit 0
  (canonical path from the ticket's own wording, not just this worktree's copy).

## Evidence

See `evidence.json` for the AC1 citation (`ops/wq3354-fleet-selfheal/install.sh:70`).
