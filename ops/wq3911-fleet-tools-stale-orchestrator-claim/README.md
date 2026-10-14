# wq#3911 — fleet_tools.yaml claims lumen_deep_research_orchestrator runs via service (it doesn't)

Found by wq#2741. `minerva/context/fleet_tools.yaml:55-58` says
`lumen_deep_research_orchestrator` "runs via service" and drains
`scratch/research_prompts` continuously (line 49's queue entry makes the same claim).
Re-verified live on this ticket, 2026-10-14:

- `systemctl list-units --all` / `list-unit-files`: no unit matching
  `*research*orchestrator*` or `*deep-research*` exists at all.
- `pgrep -f lumen_deep_research_orchestrator`: the only hits are unrelated `claude -p`
  worker processes whose dispatch prompt happens to contain the literal filename
  (a false positive from `-f` matching argv text, not the script) — no real match once
  checked PID-by-PID with `ps -p <pid> -o cmd`.
- `research/MODEL_ROUTING_AUDIT.md:168-180` (this same branch, wq#2741) independently
  confirms: no installed system/user unit, timer, root/user crontab, or `/etc/cron.*`
  entry execs it; no process or container running; `scratch/deep_research_output/`'s
  newest file is dated 2026-07-13.

## Decision (full autonomy, no Frank-gate)

Correct the documentation rather than revive the service. The audit already treats
every direct-provider script under `agent/` (this one included) as deliberately left
dormant — reviving `lumen_deep_research_orchestrator.py` would add a direct Groq/Gemini
surface outside LiteLLM, which is the thing the rest of the fleet has been actively
closing off (`research/MODEL_ROUTING_AUDIT.md` §1 header, rows 20/21). Nothing about
this ticket's evidence suggests the orchestrator is needed right now — the queue has sat
undrained since July without anyone noticing — so reviving it is a new surface for no
demonstrated demand. If the research-prompt-queue workflow is wanted again, that's a
separate ticket to re-enable it deliberately (with a LiteLLM-routed replacement, not a
restart of the direct-provider script).

## What changed

`wq3911_fleet_tools.diff` in this directory corrects the `research-prompt-queue` and
`lumen_deep_research_orchestrator` entries in `minerva/context/fleet_tools.yaml` to state
plainly that the queue is not being drained and name the ticket/date of the finding.

This is a **staged, not applied** diff: `minerva/` is a primary tree, and this worker's
guard hook refuses `Write`/`Edit` inside it (`guard.sh: BLOCKED — Write/Edit inside
PRIMARY tree /home/lumen/minerva denied (wq#1719/wq#900)`) — matching the same
sandbox boundary wq#2607/wq#2613 hit on this identical file. Hand-apply with:

```
patch -p1 -d /home/lumen/minerva < /home/lumen/foreman/ops/wq3911-fleet-tools-stale-orchestrator-claim/wq3911_fleet_tools.diff
```

(adjust `-p1`/paths to taste; the diff's hunk is small enough to apply by hand if
`patch` fuzzes on it.) After applying, re-render if the fleet has a derived doc step
(`fleet-tools-render`, per wq#1720) so `FLEET_TOOLS.md` picks up the correction.

## What this does not close

- `agents/truth_table.yaml:90`'s `lumen_deep_research_orchestrator.py::PBOX` whitelist
  entry is a separate, already-queued excision (dead PBOX env fallback) — unrelated to
  the service-claim correction here, left as-is.
- Whether to purge or archive the 50+ undrained files sitting in
  `scratch/research_prompts` is an operational cleanup call, not a doc-accuracy one;
  out of scope for this ticket.

## wq#3917 redispatch (2026-10-14, kb/ISSUE-LOG.md #1322/#1323)

wq#3917 claimed this bundle's `--verify` FAIL needed a privileged (root/sudo) install.
Re-tested live, twice: that premise is false on both counts a BUILD lane can check.
`install.sh` has no write code path at any privilege level — root changes nothing. And
the gate isn't even reachable from here: `bash install.sh --verify` isn't on this
worker's Bash allowlist, and a direct Grep/Read of `minerva/context/fleet_tools.yaml`
is refused too (`minerva/` isn't an `--add-dir` for this lane). The remaining step is
unchanged from the "What changed" section above — the hand-apply, by a worker/session
with `minerva` Write/Edit access.
