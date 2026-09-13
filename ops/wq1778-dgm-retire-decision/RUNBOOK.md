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
(`lumen-rerank-thread-watch`). Steps 1–4 below are scripted in
`remove-units.sh` (this directory) with post-removal assertions — run the
script rather than the commands by hand:

    sudo bash ops/wq1778-dgm-retire-decision/remove-units.sh

1. `systemctl disable --now lumen-dgm.timer`
2. ~~`sudo systemctl disable lumen-dgm.service`~~ — **do not**. Corrected
   wq#1779: the service is `UnitFileState=static` (no `[Install]` section), so
   `disable` is a no-op, and its only trigger is the timer already disabled in
   step 1 (`TriggeredBy=lumen-dgm.timer`, `RequiredBy=lumen-dgm.timer`,
   verified 2026-09-13). Archiving its file in step 3 is what removes it.
3. Move `/etc/systemd/system/lumen-dgm.timer` and
   `/etc/systemd/system/lumen-dgm.service` to
   `/etc/systemd/system/DISABLED-lumen-dgm.bak/` (wq#1112 archive
   convention — `mv`, not `rm`, so rollback is a `mv` back), then
   `systemctl daemon-reload`
4. `unit-drift snapshot` to fold the removal into the
   `/home/lumen/fleet-units` baseline (wq#1656 convention)
5. Verify: `systemctl list-timers --all --no-legend | grep -c dgm` → 0 (it is
   `1` today), and no new line in
   `/home/lumen/autonomy/agent5/.dgm_history.jsonl` after the next 17:00 UTC
   boundary. Baseline for that count: **93 lines**, last cycle
   `2026-09-12T17:14:34+00:00` DISCARD.

### Apply is blocked on privilege, not on scheduling (wq#1779, 2026-09-13)

wq#1779 was dispatched to a BUILD lane, which cannot run any of steps 1–4.
Two independent walls, same as ISSUE-LOG #377 hit for
`lumen-rerank-thread-watch`: (a) `foreman/harness/guard.sh:92-93` blocks
`systemctl stop|disable|mask` outright, per the deny patterns at
`foreman/harness/worker-settings.json:107-109`; (b) the lane runs as uid 1000
`lumen`, both unit files are root-owned under `/etc/systemd/system`, and
`/etc` is outside the lane's allowed directories. `fleet-exec` has no `sudo`
verb yet (`fleet-exec-policy.yaml:170-176` marks it v2), and its future
`sudo_cmds` allowlist covers only `systemctl restart|reload|start` — not
`disable`. So there is no lane in the fleet today that can close this.

Cost of the wait is measurable: DGM has run two more full cycles since the
wq#1778 decision (2026-09-11, 2026-09-12), both DISCARD, taking the history
from 91 to 93 lines.

This is now the third retirement stranded on the same gap (wq#1643
`lumen-split-watch.timer` and wq#1656/1762 `lumen-rerank-thread-watch.timer`
are both still `enabled` as of 2026-09-13). The durable fix is a
systemctl-mutate execution path for retirement work — a narrow sudoers-style
grant, or a root-side runner the foreman hands unit-removal scripts to — not
a fourth follow-up ticket.

Leave `/home/lumen/autonomy/agent5/` on disk untouched — registry, history,
benchmark, and code are the archive (see `retired_services.yaml` data
disposition). No redis drain needed: DGM sandboxes on db6 only and clears
`lumen6:*` itself after every run (`dgm.py:57-58,93`).

## Rollback

Move both files back out of `/etc/systemd/system/DISABLED-lumen-dgm.bak/`,
`sudo systemctl daemon-reload`, then `sudo systemctl enable --now
lumen-dgm.timer` — the code path is untouched by this retirement, so
re-enabling resumes daily cycles exactly as before.
