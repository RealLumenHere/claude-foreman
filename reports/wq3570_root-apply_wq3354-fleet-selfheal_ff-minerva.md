# wq#3570 — root-apply wq3354-fleet-selfheal (class 7 sh_ollama) + ff minerva primary (PR#59)

Author: claude-sonnet-5 (build worker, worktree `wq3570-a2`). This ticket had a prior
attempt (classified PERMISSION_LOOP) that appears to have completed the substantive work
before getting stuck; this pass verified every claim live and found nothing left to do.

## Part 1 — fleet-selfheal class 7 (sh_ollama) deploy

`ops/wq3354-fleet-selfheal/install.sh` ships `CODE=(... sh_providers.py sh_nodes.py
sh_ollama.py leakwatch.py)` (`ops/wq3354-fleet-selfheal/install.sh:25-26`) and
`selfheal_sweep.py` imports `sh_ollama` (`ops/wq3354-fleet-selfheal/selfheal_sweep.py:38`).
Both wq#3558's `sh_ollama.py` (CLASS 7 — ollama `keep_alive` pin wedges the scheduler,
`ops/wq3354-fleet-selfheal/sh_ollama.py:1-27`) and the updated `selfheal_sweep.py` are now
live at `/home/lumen/agents/fleet-selfheal`, byte-identical to this repo's `main` copy
(`sh_ollama.py` 5369 bytes, `selfheal_sweep.py` 3569 bytes, both installed Oct 7 11:32:15Z —
matching PR#1649's merge window).

Checked (all via `lumen-run --root`/`lumen-run`, since `/home/lumen/agents/fleet-selfheal`
is outside this worker's `--add-dir` grant and `install.sh` needs root for the unit
install + `systemctl enable`):

1. `bash ops/wq3354-fleet-selfheal/install.sh --verify` -> `VERIFY OK`, exit 0
   (unit `lumen-job-wq3570verify-1791373284`). Covers code parity for all nine `CODE[]`
   files, `selfheal.yaml` presence, both systemd units byte-matching, and
   `lumen-fleet-selfheal.timer`/`lumen-leakwatch.service` both enabled+active.
2. `python3 /home/lumen/agents/fleet-selfheal/selfheal_sweep.py --only ollama --dry-run`
   -> exit 0, no output (unit `lumen-job-wq3570sweep-1791373294`) — class 7 loads and runs
   clean against the live host.
3. Live `selfheal.yaml` was NOT edited: `sh_ollama.sweep()`'s own defaults
   (`ops/wq3354-fleet-selfheal/sh_ollama.py:72-75`) already match the ticket's stated
   class defaults (`hosts: ["http://127.0.0.1:11434"]`, `max_ttl_s: 120`,
   `ticket_after: 3`) when no `ollama:` section exists in the yaml, which is the deployed
   state (confirmed: `grep -A8 '^ollama:' selfheal.yaml` on the live file returns nothing).

No `--no-enable` / plain `install.sh` re-run was needed: the live units were already
`enabled`/`active` and `--verify` already passed, so a second apply would be a no-op
(`install.sh`'s own pytest gate + `install -o lumen -g lumen` are idempotent, but re-running
a root unit-reload/enable step with nothing to change is not worth the extra root action).

## Part 2 — minerva PR#59 + ff primary

`/home/lumen/minerva` local `main` is at `70cc936240efb03ae0cb3b2616d3c293d651f152`,
identical to `origin/main` (`git rev-parse HEAD origin/main` returns the same sha twice;
`git log origin/main..main` is empty) — the ticket's described blocker ("local main carries
unpushed bea3f0c") is already resolved: bea3f0c's content landed as `70cc936`
(`fix(prefs_block): force wq add past dup-title guard`, landed via a fresh branch/PR per
`/tmp/fworker-wq3570/pr-body-prefs-block.md` rather than a force-push, since bea3f0c and
origin/main had diverged one commit each way).

PR#59's commit, `59458d32768745cf12a84fb06fd48bf86eea1734`
(`fix(embed): drop the keep_alive pin that wedges ollama`, wq#3558), is reachable from
`main` (second commit back). It drops the `keep_alive: '30m'` literal from the embed call
in `minerva_skills.py` (`diff --git a/minerva_skills.py`: `-json={'model': EMBED_MODEL,
'input': text[:4000], 'keep_alive': '30m'}` -> `+json={'model': EMBED_MODEL, 'input':
text[:4000]},  # no keep_alive: wq#3558`) and leaves explanatory comments in
`minerva_memory.py` and `minerva_verify.py`.

`/home/lumen/bin/lumen-pr ffprimary /home/lumen/minerva` ->
`lumen-pr: primary /home/lumen/minerva already at origin/main (70cc936)`, exit 0 — nothing
to fast-forward.

`grep -n keep_alive minerva_memory.py minerva_skills.py minerva_verify.py` on the live tree:

```
minerva_memory.py:64:    # No keep_alive (wq#3558): a per-request keep_alive on an already-loaded
minerva_skills.py:46:            json={'model': EMBED_MODEL, 'input': text[:4000]},  # no keep_alive: wq#3558
minerva_verify.py:320:        # No keep_alive: a per-request pin starves chat model loads on Lumen (wq#3558).
```

All three hits are comments; no live `keep_alive` call remains in these files.

## Side note: stale busy key

On start, `lumen_busy check` returned a stale `{"busy": true, "task": "wq3571 verify
wq3558 ollama scheduler wedge", "phase": "landing-pr", "pid": "1180708"}` — a different
ticket's lock. `ps -p 1180708` found no such process; cleared the key before starting
this ticket's own work. Not otherwise in scope here (wq#3571 is a separate ticket).

## Verdict

Both halves of wq#3570 were already true live before this session took any mutating
action — most likely completed by the prior PERMISSION_LOOP attempt before it got stuck.
This pass re-verified every claim against the live hosts/repos directly (not by trusting
the prior attempt's notes) and found no drift to fix.
