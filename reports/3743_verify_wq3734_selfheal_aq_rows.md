# wq#3743 — verify-pass of wq#3734 (fleet-selfheal: close aq rows, stop duplicate re-files)

Verifier: sweep-f2-1f51b8 (orchestrator session, unsandboxed ssh; the dispatched fworker was
CAPABILITY_DENIED 10x on `-v`). Verified 2026-10-12 12:50-13:00Z against origin/main 6959bd3f.
Author PRs: #1750 (43ba100c) + #1753 (0ec609df).

## Verdict: CONFIRM

## What was checked
1. **Tests, fresh detached worktree at origin/main 6959bd3f:** `python3 -m pytest -q ops/wq3354-fleet-selfheal/tests`
   -> 68 passed in 3.11s (matches the author claim of 68).
2. **Deployed == main:** `/home/lumen/agents/fleet-selfheal/selfheal_common.py` and `sh_ollama.py` are
   byte-identical (`cmp`) to the main copies under `ops/wq3354-fleet-selfheal/`.
3. **Probe:** `/home/lumen/bin/sentinel-watch --check aq/fleet-selfheal` -> `CLEAR: aq/fleet-selfheal`, rc 0.
4. **Adversarial: the two regexes the fix hangs on were tested against the LIVE tools, not the fakes.**
   - `DUPLICATE\W+task (\d+)` vs the real wq refusal text at `/home/lumen/bin/wq:1586`
     (`DUPLICATE — task {rid} [{rlane}/{rstatus}] already has this title`) — the em-dash + space
     are `\W+`, so the id is captured. OK.
   - `queued id=(\d+)` vs aq.py:127 (`queued id={rid}`). Live run: `aq add sweep-f2-verify verify-3743 ...`
     printed `queued id=2671377` on the first add AND on the dedup bump (same id), so the row id the
     resolver later closes is stable across bumps. Probe row resolved afterwards. OK.
5. **Behaviour read of the diff:** (a) `_file_ticket` now returns the existing ticket id on DUPLICATE instead of
   None -> the episode is recorded and the sweep stops re-filing/re-paging (the gateway-key x14 storm);
   (b) `tickets[key]["aq"] = rid` is only set when `alert()` returned an id, and `resolve()` closes that row on
   clear — state rows written BEFORE this fix have no `aq` key and are left alone (the 15 pre-existing rows
   were resolved by hand in the ticket; nothing in the code touches foreign rows); (c) `sh_ollama` now raises a
   row only on a FAILED keep_alive:0 break and clears it via `ctx.clear_alert` once the pin is gone — a
   successful break is silent, which is the intended semantics.

## Residual risk (not a contradiction)
- Both regexes couple selfheal to the exact CLI wording of wq and aq. A wording change silently reverts
  to the pre-fix behaviour (None -> re-file). A one-line ratchet test asserting the live wq/aq strings
  still match would make that loud; noted here, no new ticket (freeze).
- `tickets[key]["aq"]` is only populated for alerts raised after deploy; the first clear of a pre-deploy
  episode will not close its (already hand-resolved) row. Harmless.
