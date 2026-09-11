# wq#1778 — DGM optimizes a config with no live consumer: retire lumen-dgm

Found by wq#1715 F2: `config_registry.json` is read by exactly two live
programs — `agent5/dgm.py` and `agent5/a5_common.py`, i.e. DGM's own
benchmark harness. The production consumers (`lumen-planner`,
`lumen-generator`, `lumen-evaluator`) are `LoadState=not-found` per the
wq#1643 sweep (`ops/wq1643-split-watch-retire-agent5/RUNBOOK.md`), and the
agent5 split is formally retired (`docs/fleet/retired_services.yaml`,
`agent5-split` entry). `lumen-dgm.timer` is the last agent5-split process
still firing.

## Evidence (2026-09-11)

- `systemctl status lumen-dgm.timer`: enabled, active(waiting), fires daily
  17:00 UTC; `lumen-dgm.service` runs
  `python3 /home/lumen/autonomy/agent5/dgm.py run` (TimeoutStartSec=7200).
- `dgm.py` sandbox spawns its own copies of planner/generator/evaluator.py
  on redis db6 (`dgm.py:60-93`) — it does not depend on the retired units,
  which is exactly why it kept running quietly after the split died. Its
  output config, however, flows nowhere: promotion (`dgm.py approve`,
  human-gated) only rewrites `config_registry.json`, whose only readers are
  the harness itself.
- `.dgm_history.jsonl`: 91 cycles 2026-06-16 → 2026-09-10. Decisions:
  85 discard, 3 PROMOTE (2026-08-18, -20, -24), 2 pending,
  1 discarded-by-human. Zero promotes in the 18 days since 08-24; the last
  four cycles' bootstrap CIs all straddle zero (e.g. 09-10: mean −0.0431,
  ci[−0.099, 0.0108]). The one-gene search (`target=generator_sys` on all
  86 v2 cycles, wq#1715 F1 / wq#1777) has plateaued even on its own
  benchmark.
- `config_registry.json`: active `v3-evaltokens-20260807`, 10 versions,
  **no pending candidate awaiting approval** — nothing in flight to lose.
- Daily cost of leaving it: one proposer LLM call + paired benchmark
  (10–12 prompts × base+candidate generations × LLM grading), ~all of it
  spent producing DISCARD lines about a prompt nothing reads.

## Decision

**Retire `lumen-dgm.timer`/`lumen-dgm.service` with the rest of the agent5
split.** Made under the standing full-autonomy instruction — recorded here,
not parked on Frank.

Rewiring the genome onto a live consumer was the alternative and was
rejected, not ignored:

- The candidate hosts (fworker worker prompt in `foreman/harness`,
  prompt-forge `templates.py`) are **code changed by PR** — prompt-forge's
  README states its static template text "changes by PR" by design, with
  fleet facts measured at generation time. Neither has a benchmark harness,
  a versioned config registry, nor a human-gated promote path.
- "Rewire" would therefore mean designing a new genome format, a new graded
  benchmark with ground truth for session-prompt/ticket-prompt quality, and
  a new promotion mechanism (PR-opening bot). That is a fresh project to be
  proposed on its own merits — with the 91-cycle history here as its prior
  evidence — not a config change to DGM.
- DGM's own trajectory argues against carrying the machinery forward as-is:
  3 promotes in 91 cycles, all in one August week, none since.

Consequence for **wq#1777** (DGM v2 rotation wheel, explicitly dependent on
this outcome): do **not** implement the wheel — it would multiply mutation
surface on a genome with no consumer. Amend `DGM_V2_ROTATION_DESIGN.md` to
record the retirement, or close wq#1777 against this runbook.

## Apply (follow-up lane — systemctl mutate is deny-listed on build lanes)

Same restriction and pattern as wq#1643 (`lumen-split-watch`) and wq#1656
(`lumen-rerank-thread-watch`). From a lane with systemctl-mutate access:

1. `sudo systemctl disable --now lumen-dgm.timer`
2. `sudo systemctl disable lumen-dgm.service` (oneshot; disable so nothing
   re-triggers it)
3. Move `/etc/systemd/system/lumen-dgm.timer` and
   `/etc/systemd/system/lumen-dgm.service` to the retired-units archive
   (convention: `/etc/systemd/system/DISABLED-*.bak/`, per wq#1112), then
   `sudo systemctl daemon-reload`
4. `unit-drift snapshot` to fold the removal into the
   `/home/lumen/fleet-units` baseline (wq#1656 convention)
5. Verify: `systemctl list-timers | grep -c dgm` → 0, and no new line in
   `/home/lumen/autonomy/agent5/.dgm_history.jsonl` after the next 17:00 UTC
   boundary.

Leave `/home/lumen/autonomy/agent5/` on disk untouched — registry, history,
benchmark, and code are the archive (see `retired_services.yaml` data
disposition). No redis drain needed: DGM sandboxes on db6 only and clears
`lumen6:*` itself after every run (`dgm.py:57-58,93`).

## Rollback

`sudo systemctl enable --now lumen-dgm.timer` after restoring the two unit
files — the code path is untouched by this retirement, so re-enabling
resumes daily cycles exactly as before.
