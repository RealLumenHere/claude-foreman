# wq#3342 verify: wq#3266 clamp DC call window to 55s + detach start_process children

Archetype 8 adversarial verify pass. Author: claude-opus-5-5. Verifier: claude-sonnet-5@high
(fresh instance).

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1546, merged to `main` as
`c149f3a7` (content-identical to the PR head `93496d85` — `git diff 93496d85 c149f3a7`
is empty). Files: `ops/wq3103-commander-guard/{dc-call-window.cjs (new),
commander-preload.cjs, apply.sh, test_call_window.mjs (new), accept_real_dc.mjs (new),
fixture_dc_stdio.cjs (new), fixture_mcp_stdio.py, test_commander_guard.mjs, README.md}`.

## What wq#3266 claimed

RCA wq#3264 found two ways Commander drops a Worker-fronted call: Mode A (supergateway
reaps the request's process group/session once the response ends, killing a `start_process`
child that's supposed to outlive the call) and Mode B (the Worker's 70s abort closes the
request mid-flight if DC hasn't answered). Fix: `dc-call-window.cjs`, loaded by
`commander-preload.cjs` in two roles —
- gateway role: intercepts the JSON-RPC line written to DC's stdin and clamps
  `timeout_ms` on `start_process`/`interact_with_process`/`read_process_output` to
  `MAX_CALL_WINDOW_MS` (default 55000) minus elapsed time since the DC spawn minus a
  500ms reply margin, so the window is measured from DC's cold start, not from when DC's
  own timer starts. Logs one WARN line to stderr and one row to `~/.dc-handles.db`
  (`call_window_warn` table) per clamp.
- dc role: spawns `start_process` shells `detached` (setsid) so supergateway's
  per-request reap spares the new session; documented limit: a detached job's stdout is
  still piped to the DC that spawned it, so a chatty job still dies to SIGPIPE once that
  DC is reaped — only silent/file-logging jobs survive indefinitely.
- Claimed: `test_call_window 16/16`, `test_commander_guard 17/17`, `accept_real_dc 6/6`
  against real DC 0.2.46, with `timeout_ms=120000` returning at 54.5s (<=55s).

## What I checked

- Read `dc-call-window.cjs`, `commander-preload.cjs`, `apply.sh`, `fixture_dc_stdio.cjs`,
  and all three test files cold, tracing the `windowLeft()` clamp math, the `cp.spawn`
  interception in both `installGateway`/`installDc`, and the TERM-marker match
  (`terminal-manager.js` sets `TERM=xterm-256color` on DC's shell spawns; nothing else DC
  spawns sets it, per the comment — matches `fixture_dc_stdio.cjs`'s own spawn, which is
  shaped to mirror it).
- Ran all three test suites directly against the real binaries (not a description of the
  PR's claim — actually executed, via `python3 subprocess` wrapping `node` per
  ISSUE-LOG#1143's documented workaround for this worker profile's `node <script>`
  refusal):
  - `test_call_window.mjs` (real supergateway 3.4.3 + a DC-shaped stdio fixture,
    `MAX_CALL_WINDOW_MS=3000`): **16/16 passed**. Confirmed: clamp math
    (`timeoutSeen` 2440-2441ms, i.e. `3000 - startup - 500margin`), one WARN row per
    clamp in `DC_HANDLE_DB`, one WARN line on stderr, all three named tools clamped,
    a sub-window `timeout_ms` passed untouched, non-clamped tools (`read_file`)
    untouched, DC shells inherit neither `NODE_OPTIONS` nor
    `COMMANDER_PRELOAD_ROLE`, and the detach itself: `sleep 241` (`r.pid`) still alive
    via `kill -0` after the DC process and its in-session control command were both
    reaped.
  - `test_commander_guard.mjs`: **17/17 passed** (bind, shadow/enforce mode-file flip
    without restart, fail-closed on garbage/unreadable mode file, child does not inherit
    gateway `NODE_OPTIONS` but does see `childPreloadRole: dc`, misconfig exits 78).
  - `accept_real_dc.mjs` (real `desktop-commander` + real `supergateway`, default
    `MAX_CALL_WINDOW_MS=55000`, scratch `HOME`/`DC_HANDLE_DB`, nothing live touched):
    **6/6 passed**. `start_process 'sleep 241' timeout_ms=4000` then `kill -0` 15s later
    succeeds through the preloaded gateway; the identical probe through a gateway
    **without** the preload finds the PID dead 15s later (the negative control — this
    satisfies wq#2630: the probe is drawn from outside the fix's own population and
    demonstrably *can* fail). `timeout_ms=120000` returned at **54.5s** (clamped to
    53350ms) — matches the PR's claimed number exactly, under the 55s bound and under
    the Worker's 70s abort. One WARN row logged with the correct `requested_ms`/
    `clamped_ms`.
- Verified no drift between the shipped repo files and what's actually installed on
  Lumen: `diff /home/lumen/bin/dc-call-window.cjs
  ops/wq3103-commander-guard/dc-call-window.cjs` and the same for
  `commander-preload.cjs` are both empty (byte-identical), file mtimes `Oct 1 15:41`.
- Verified the PR's "not active until Commander restarts, Frank's go" claim is honest,
  not a disguised false "done": `systemctl show desktop-commander-mcp -p
  ActiveEnterTimestamp` returns `Thu 2026-10-01 03:44:23 UTC`, well before the
  15:41 file install — the live unit has not picked up the new preload, consistent with
  the README's explicit statement that it is staged, not yet activated.
- Read the `apply.sh` diff: `dc-call-window.cjs` is installed before
  `commander-preload.cjs` (which `require`s it) in the `deploy` install step, and
  `test_call_window.mjs` was added to the `deploy` preflight test gate alongside the
  pre-existing suites — correct ordering, no gap in the deploy-time safety gate.
- Adversarial angles tried and ruled out:
  - Clock-skew / near-window-end races: `windowLeft()` floors at `MIN_TIMEOUT_MS=1000`,
    no negative-timeout or crash path.
  - Batched-write bypass: `clampLine()` does one `JSON.parse(line)` per `stdin.write()`
    call and silently no-ops (returns the line unmodified) if that single write contains
    more than one NDJSON message. This would be a real bypass if supergateway ever
    batches multiple JSON-RPC messages into one `write()`, but empirically, across 16 + 6
    real-binary test calls (real supergateway 3.4.3, real DC 0.2.46), every clamp fired
    and every clamped value matched the expected math — one message per write holds in
    practice for the versions actually deployed. Noting as a fragility worth a comment,
    not a confirmed defect: nothing in this PR's scope exercises or claims to exercise
    multi-message batching.
  - The documented SIGPIPE limitation (chatty detached jobs still die once DC is reaped)
    is disclosed in both the code comment and the README, not a hidden gap, and is
    orthogonal to this ticket's acceptance criterion (`kill -0` liveness of a *silent*
    `sleep`, which is exactly what `accept_real_dc.mjs` probes).

## Contradiction found

None. Every number in the PR's commit message and README (`16/16`, `17/17`, `6/6`,
`54.5s`, clamped to `53192`/`53350`ms on different runs) reproduces against the real
binaries, the negative control in `accept_real_dc.mjs` is genuine (fails as designed
without the preload), and the installed-but-inactive state matches what's claimed.

## Action taken

Confirming on the board. No escalation.
