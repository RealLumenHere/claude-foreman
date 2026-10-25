# wq#4629 — Watchdog scripts for Commander long-command survival

## TL;DR

The ticket's framing ("Commander 90s timeout kills builds") is close but the
mechanism, and most of the fix, are already on this branch's ancestry:

- The kill is **not** a flat 90s client timeout. It's a Cloudflare Worker
  aborting the `tools/call` at 70s, which cascades into a process-group
  SIGTERM on Commander's gateway. Root cause: `reports/wq3264_mcpw_commander_drops_rca.md:9-29`.
- The survival fix already shipped twice, at two layers:
  1. **Gateway-side clamp + detach** — `ops/wq3103-commander-guard/` (wq#3266,
     verified wq#3342): clamps `timeout_ms` to 55s and spawns jobs `setsid`.
  2. **The actual long-job tool the fleet uses today** — `/home/lumen/bin/lumen-run`:
     launches builds as transient `systemd-run` units (reparented to PID 1,
     outside the gateway's reach entirely, not a detach trick that can still
     get SIGTERM'd), tracked by **unit name**, with a `wait` subcommand
     clamped to the same ~55-70s call window so an agent can safely re-poll.
- **What is missing**, and what this ticket's bundle (`ops/wq4629-commander-watchdog-a1/`)
  actually adds: nothing today *notifies* on completion. Every existing tool
  is pull-only — an agent (or Frank) has to remember to come back and ask.
  That's the real "watchdog" gap, and it's what's implemented and tested
  here.
- "Inject the result back into a Claude chat" is not literally possible —
  nothing can push into an already-running model turn. The three real
  channels are documented in §6, and the closest one (spawn a fresh
  `claude -p` seeded with the result) is implemented in the bundle.

## 1. The failure mode, precisely

`reports/wq3264_mcpw_commander_drops_rca.md:9-25`: `desktop-commander-mcp.service`
runs `supergateway` stateless; each HTTP request spawns a Commander child in
its own process group. `supergateway`'s `reapChild` sends `SIGTERM` to
`-pgid` on either `res.on('close', ...)` (client gone — "Mode B") or
`res.on('finish', ...)` (call returned — "Mode A"), then `SIGKILL` after
`SUPERGATEWAY_REAP_GRACE_MS` (default 2000ms).

The 70s/90s numbers' real origin, per `reports/wq3264_mcpw_commander_drops_rca.md:29`'s
own citation of `projects/mcp-oauth-worker/src/router.ts:29,156` (that Worker's
repo is not checked into this worktree, so this is the report's citation, not
independently re-verified here):
`TOOLS_CALL_TIMEOUT_MS = 70_000` — the Cloudflare Worker in front of Commander
aborts any `tools/call` at 70s via `AbortSignal.timeout(timeoutMs)`.
`cloudflared` then logs `context canceled`, and supergateway's `close`
handler reaps the still-running job (Mode B). Measured correlation
(`reports/wq3264...:62-67`): 86% of real cancels have a call aged 66-72s in
flight. The "90s" the ticket names is in the right neighborhood but the
actual trigger is the Worker's 70s abort plus reap grace, not a Commander- or
SSH-side 90s constant.

**Why `nohup`/`disown` don't save a build**
(`reports/wq3264...:87-90`, verbatim): *"use `setsid`/`detached` in a new
session. `nohup` alone does not help: it ignores SIGHUP, not the group
SIGTERM."* `SIGTERM -pgid` hits every process still in the spawning
process group regardless of whether it ignores `SIGHUP` — only a real
session/process-group break (`setsid`) or full reparenting out of the
gateway's tree (`systemd-run`) survives.

## 2. What's already shipped

**Layer 1 — gateway clamp + detach** (`ops/wq3103-commander-guard/`, wq#3266,
verified wq#3342 — `reports/wq3342_verify_wq3266_dc_clamp_timeout_ms_55s_detach.md`):

- `dc-call-window.cjs` (loaded via `commander-preload.cjs`) runs two roles
  (`reports/wq3342...:17-28`):
  - gateway role: clamps `timeout_ms` on `start_process`/`interact_with_process`/
    `read_process_output` to `MAX_CALL_WINDOW_MS` (default 55000) minus
    elapsed time since the DC spawn minus a 500ms reply margin.
  - dc role: spawns `start_process` shells `detached` (`setsid`) so
    supergateway's per-request reap spares the new session.
- Proven: `timeout_ms=120000` returns at 54.5s, clamped to 53350ms
  (`reports/wq3342...:29-30,56-65`); a negative-control gateway without the
  preload finds the PID dead 15s later.
- **Disclosed limitation** (`reports/wq3342...:25-28,91-94`): a detached
  job's stdout is still piped through the spawning DC process, so a *chatty*
  job dies to `SIGPIPE` once that DC gets reaped — only silent or
  file-logging jobs survive indefinitely. `ops/wq3103-commander-guard/README.md:22,48`
  documents the clamp and a `start_process 'sleep 241' timeout_ms=4000` demo.

This is the right fix for "survive one Commander call," but it is a
transport-layer patch: it doesn't give an agent any way to find out the job
finished later, and the chatty-stdout caveat means it isn't safe for an
arbitrary 30-minute build's console spam.

**Layer 2 — the actual tool for long jobs — `/home/lumen/bin/lumen-run`**:

Header comment (`lumen-run:2-17`, paraphrased close to verbatim): launches a
job over Commander so it survives the reaper; `nohup`/`setsid`/`disown`
alone do **not** survive a SIGKILL to the process group or cgroup — only
`systemd-run` reparenting the job to PID 1's tree, *outside the gateway's
reach*, does. Jobs are tracked by **unit name**, never PID.

- `lumen-run start -- CMD` (`lumen-run:67-115`): builds a transient unit
  `lumen-job-<slug>-<epoch>` via `sudo -n systemd-run --unit=... --collect
  --slice=lumen-workers.slice ...` (`lumen-run:93,112-113`, the
  `lumen-workers.slice` isolation is wq#2605, cited inline at `lumen-run:87-92`),
  appends a `LUMEN_JOB_EXIT=$rc` sentinel line so `status`/`wait` can parse
  the real exit code out of the journal (`lumen-run:99-111`).
- `lumen-run status UNIT` (`lumen-run:116-128`): `RUNNING` / `DONE:0` /
  `FAILED:<code>` / `GONE`, parsed by grepping `journalctl -u UNIT` for the
  sentinel line.
- `lumen-run wait UNIT [budget]` (`lumen-run:140-165`): polls
  `systemctl is-active` every 3s (**this is the filesystem/process-state
  polling the ticket asks about — already implemented, process-table-based
  rather than filesystem-mtime-based**), clamped to `LUMEN_RUN_WAIT_CAP`
  (default 55, `lumen-run:145-151`) specifically because *"a Commander call
  is killed by the Worker's 70s abort, so a wait longer than that never
  returns an answer (6 of 31 cut calls in 24h were exactly `wait <unit>
  840`)"* (`lumen-run:141-143`). On exit 124 (still running), the caller is
  told to re-poll (`lumen-run:149-150,156-160`).

So: launching a 30-minute `build-limpet.sh` through Commander today already
works, if it's launched via `lumen-run start` and the agent re-polls
`lumen-run wait` a few times rather than issuing one 30-minute Commander
call. The gap is everything downstream of "the agent forgot to re-poll, or
isn't even in the same session anymore" — there's no push notification.

## 3. Is Commander (desktop-commander-mcp) still in use?

Yes — relocated, not retired. `docs/fleet/retired_services.yaml:319-330`:
the Lumen-local `desktop-commander-mcp-lumen` unit is `LoadState=masked`,
retired 2026-10-13, reason verbatim: *"Commander now runs on Horus
(mcp-proxy), reached via the mcpw.lumennode.uk Cloudflare Worker, not on
Lumen. The mcp-backend.lumennode.uk -> :3100 cloudflared route was removed
(wq#3781) and nothing connects to :3100, so the Lumen-local supergateway
unit was stopped and masked (wq#3805)."* `reports/wq3846.../wq3847...md:17-20`
confirm this was a self-healer watch-table cleanup (dropping a now-masked
unit from a restart-if-dead list), not a decision to stop using Commander.
A second, parallel path exists too: `reports/wq4330_remote_dc_lumen_test.md`
(verdict "WORKS", 2026-10-19) verifies a hosted `desktopcommander.app` relay
direct to Lumen, with an explicit "do not tear down Horus" note (line 45).
`research/WQ4470_CLAUDE_CODE_COMMANDER_EVAL.md:3,68-75` separately confirms
Commander was evaluated for replacement by the Claude Code CLI and rejected
for its claude.ai-browser-facing role — Commander stays.

Net: the 70s-abort failure mode in §1 is live and current, not a retired
problem.

## 4. External/community prior art

- **Desktop Commander MCP's own design already has an async model**: its
  public docs describe "Command timeout and background execution support"
  and "execute with timeout -> continues in background -> read output by
  PID" (per its GitHub README and third-party setup guides, confirmed via
  `fleet-search web`). This is the same shape as `lumen-run status`/`log` —
  a long job keeps running after the call returns, and a *separate* call
  reads its output by handle. The fleet's `lumen-run` reimplements this
  using systemd unit names instead of raw PIDs specifically because a raw
  PID doesn't survive the gateway's process-group reap (§1) — DC's own
  PID-based handle is exactly as vulnerable to that reap as a bare `nohup`
  job would be, which is presumably why `ops/wq3103-commander-guard` had to
  patch the gateway layer independently.
- **Filesystem polling vs `inotifywait`**: polling (what `lumen-run wait`
  already does, and what a naive watchdog would do against a result file)
  is simple, dependency-free, and bounded CPU cost at a 3s period — fine for
  a single job. `inotifywait`/`systemd.path` is event-driven (confirmed via
  `fleet-search web`: "systemd uses inotify under the hood... registers an
  inotify watch" on a `.path` unit) — zero cost while idle, fires
  immediately, but needs either `inotify-tools` installed or a systemd unit
  installed (root). For *one* long-running build, the difference is noise;
  it starts to matter once there are many concurrent watched jobs or the
  reactor needs to idle for hours between events — which is exactly the
  "watch a done-directory, not a single job" design in §5.
- **"Looping Tasks"-style agent scripts**: no single script by that exact
  name turned up in a targeted search, but the pattern it names — a detached
  loop that outlives one agent turn and resumes/re-polls — is already native
  to this fleet twice over: `lumen-run`'s `systemd-run` detach (§2) and the
  Claude-session-level `lumen_busy task_start` (`CLAUDE.md` busy-key
  protocol; referenced at `design/wq4355_perplexity_computer_dissection.md:109-112`
  as "the BUILD-worker / `lumen_busy task_start` / detached-systemd pattern").
  Nothing new to add there beyond what's already running.
- **tmux shared-terminal injection**: viable in principle (a watchdog could
  `tmux send-keys` a result string into a pane Commander is already
  attached to), but it's strictly worse than DC's/`lumen-run`'s native
  handle-based model for this fleet: it requires a *persistent* tmux session
  that both the watchdog and Commander agree to share, re-introduces a
  PID/pane handle that's just as reapable as a bare process, and buys
  nothing a `read_process_output`/`lumen-run log` call doesn't already give.
  Not pursued further.

## 5. The actual gap, and what this ticket's bundle adds

Every tool above is **pull-only**: `lumen-run status`/`wait`,
`read_process_output`, `journalctl` — all require something to come back and
ask. Nothing fires a notification, and nothing bridges "a systemd unit
finished" into a filesystem event a `.path` unit could react to (units
don't have a native "I dropped a file when I exited" signal).

`ops/wq4629-commander-watchdog-a1/` adds exactly that, as two small,
additive pieces (full detail in that directory's `README.md`):

1. `lumen-job-notify.sh` — launched detached (never through Commander, since
   it blocks for the job's full runtime by design). Loops
   `lumen-run wait UNIT` past `TIMEOUT` results until a terminal state, then
   writes a completion marker JSON into a done-dir. Implementation:
   `ops/wq4629-commander-watchdog-a1/lumen-job-notify.sh:34-41`.
2. `commander-job-notify.path` + `.service` — **the systemd-oneshot-triggered-by-inotify
   the ticket asks about, directly answered: yes**, and the fleet already
   has a working template for exactly this shape:
   `research/systemd/lumen-research-sandbox.path:4-8` uses
   `DirectoryNotEmpty=` (systemd.path's inotify wrapper) to wake a
   `Type=oneshot` service (`research/systemd/lumen-research-sandbox.service:10`)
   whenever a proposal file lands in a watched directory. This ticket's
   `commander-job-notify.path` reuses that exact pattern, watching
   `/home/lumen/var/commander-watchdog/done` instead. The woken oneshot,
   `drain_done_dir.sh`, `ntfy-send`s the result and clears the marker so the
   `.path` unit re-arms cleanly (`ops/wq4629-commander-watchdog-a1/drain_done_dir.sh:12-29`).

## 6. "Inject the result back into a Claude chat or `claude -p` session" — reality check

There is no mechanism to push content into an already-running model turn.
The three channels that actually exist:

1. **The same or a resumed agent polls again** — the status quo today
   (`lumen-run wait`/`status`), bounded by whoever remembers to ask.
2. **A fresh `claude -p "<result>"` invocation, spawned by the watchdog once
   the job completes.** This is the closest real analogue to "injection":
   not pushing into a live session, but starting a new one pre-loaded with
   the news. Implemented and tested here —
   `ops/wq4629-commander-watchdog-a1/drain_done_dir.sh:21-23`, gated behind
   `COMMANDER_WATCHDOG_NOTIFY_CLAUDE=1` so it's opt-in (spawning a billed
   session automatically on every build completion is a real cost, left to
   whoever deploys this to decide per job).
3. **`ntfy` to a human**, who relays — what `drain_done_dir.sh` does by
   default via `/home/lumen/bin/ntfy-send`.

## 7. Evidence

`pytest ops/wq4629-commander-watchdog-a1/test_commander_watchdog.py` — 2
passed. Stubs `lumen-run`/`ntfy-send`/`claude` so the test exercises both
scripts end-to-end (re-poll past a `TIMEOUT`, write the marker, notify,
inject, clear the marker so the `.path` unit would re-arm) with no real
systemd unit, network call, or fleet credential.

## 8. Follow-ups (not done here — out of this ticket's scope)

- Deploying `commander-job-notify.path`/`.service` needs root
  (`/etc/systemd/system/*`, `daemon-reload`) — this BUILD sandbox has no
  privilege-escalation verb, so, per the `ops/wq2606-mcp-gateway-watchdog-a1`
  precedent (ISSUE-LOG wq#2606), the units are shipped here as a citable,
  tested proposal rather than a live install.
- A longer-term, lower-moving-parts alternative worth a future ticket:
  teach `lumen-run start` an optional `--notify` flag that sets
  `systemd-run -p ExecStopPost=...` directly, so the unit notifies on its
  own exit with no separate watcher process at all. Not attempted here —
  it changes a tool every long job on the fleet depends on, which is a
  bigger blast radius than a RESEARCH ticket should take on unsupervised.
