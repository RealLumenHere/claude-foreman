# wq#1882 — despag P1-exit: full conformance on main, both harnesses, one process

One `python3 -m despag` process (PID 952806, `127.0.0.1:8795`), started from a fresh
worktree of `main` (`bae1e17`), data isolated under
`despag/reports/rundata/` for the run (deleted afterward — scratch, not a deliverable).
Server stdout/stderr: `despag/reports/p1exit_server.log`. Reproduce with
`despag/tools/run_p1_exit.py` (`serve` + `conformance` subcommands — same env wiring
this run used).

## Results

| target | class | result | report |
|---|---|---|---|
| litellm-mechanical (`chrn_litellm-mechanical`) | core | **40/40 passed, CONFORMANT** | `despag/reports/p1-exit_litellm.json` (console: `p1exit_litellm_run.log`) |
| claude-max (`chrn_claude-max`) | core | **40/40 passed, CONFORMANT** | `despag/reports/p1-exit_claudemax.json` (console: `p1exit_claudemax_run.log`) |

`--class core` is the whole scope: every D/H/S/C/T-prefixed check in
`uhp_conformance/checks.py` has `class="core"` (verified by listing all `@check(...)`
class args) — there is no separate class to add. `extended`/`full` were not run: the
server's own `/v1/uhp` declares `conformance_class: "core"` with
`session_listing`/`harness_management`/`files_input`/`files_output`/`session_sharing`
all `false` (`despag/app.py:367-379`), so those checks are out of Phase-1's claimed
scope, not a gap in this run.

**No FAIL or ERROR in either run — no fix tickets filed.** wq#1852's post-fix baseline
was 38/40 (T-08 failing, S-09 skipped); both passed cleanly here. Root cause of that
difference: this BUILD worker's PATH lacks `/home/lumen/.npm-global/bin`, so the first
attempt's `claude` subprocess spawn 404'd (`spawn: [Errno 2] No such file or directory:
'claude'`) and every real task failed in ~0s, which is what made T-08 fail and S-09 skip
(too fast to prove streaming was progressive). Logged as ISSUE-LOG #374 (append blocked
by the kb primary-tree guard — wq#1719/wq#900 — see below; entry text is in the ticket
notes for whoever has kb write access to port over). Fixed for this run with
`DESPAG_CLAUDE_BIN=/home/lumen/.npm-global/bin/claude`
(`despag/tools/run_p1_exit.py:_configure_env`) — not a despag code change, since
production despag is expected to run with that dir already on `PATH`. **wq#1885**
("bisect wq#1812 admission gate against T-08") should re-check against a
`DESPAG_CLAUDE_BIN`-correct environment before assuming a real admission-gate bug: this
run's clean T-08 pass suggests the earlier failure may have been the same PATH gap, not
the admission gate.

## Concurrency (S-04/S-09/C-03 legs)

A background loader (`despag/reports/p1exit_noise.log`) kept two extra
`chrn_litellm-mechanical` background responses in flight throughout the litellm-
mechanical run, launching new ones every ~3-10s from `1789265736` to `1789265774`
(epoch); the suite's own JSON report was written at `1789265759` (`generated_at:
2026-09-13T02:15:59Z` in `p1-exit_litellm.json`), inside that window — so the suite's
own session plus the two noise sessions gave >=3 concurrent sessions for the whole run,
including whichever moment it reached S-04/S-09/C-03.
`DESPAG_MAX_CLAUDE_CONCURRENT=8` (server startup log) had headroom to spare.

## Real cancel, process-group kill

Separate from the suite's own C-03 (which only checks the UHP-level status transition),
ran an explicit OS-level check: started a `chrn_litellm-mechanical` background response,
captured the despag server's new child PID via `psutil` (`994631`, name `claude`),
called `POST /v1/responses/{id}/cancel`, and confirmed both (a) the API status reached
`cancelled` and (b) PID 994631 no longer existed (`psutil.pid_exists` false) within a
few seconds — the actual OS process group was killed, not just the DB row. This is
`despag/runner/turn.py:121-136` (`_kill`): `os.killpg(os.getpgid(self.proc.pid),
signal.SIGKILL)` at line 133, reachable because `start_new_session=True` at spawn
(`turn.py:165`) makes the child's pid its own pgid.

## Gateway resource footprint

PID 952806, measured via `psutil` (direct `/proc` reads are outside a BUILD worker's
bounded filesystem, so `ps`/`cat /proc/.../status` can't be used here):

| point | RSS | open fds |
|---|---|---|
| after startup, before any load | 61,792,256 B (60.4 MiB) | 7 |
| after litellm-mechanical suite + concurrency load + cancel test | 64,495,616 B (61.5 MiB) | 7 |
| after claude-max suite (final) | 64,794,624 B (61.8 MiB) | 7 |

+~2.9 MiB RSS growth over 80 conformance checks + noise load + one explicit cancel, fd
count flat at 7 throughout — no fd leak observed.

## `GET /v1/uhp` — `lumen.budget` (final snapshot)

```json
{
  "session_pct_used": 26,
  "weekly_pct_used": 58,
  "reserve_pct": {"session": 30.0, "weekly_all": 15.0, "fable_weekly_scoped": 10.0},
  "resets_at": "2026-09-13T03:20:00.827417+00:00",
  "paused_until": null
}
```

Wired at `despag/app.py:367` (`discovery()`) / `:394` (`body["lumen"] = {"budget":
admission.budget_snapshot()}`), values computed live in `despag/admission.py:257-273`.

## OAuth token: read per spawn, never held

Confirmed by code path, not just discovery output: `despag/backends/claude_max.py:37`
(`build_turn`) calls `fworker.build_env(...)` fresh on every turn; `fworker.py:1070`
calls `read_oauth_token()` inside that same `build_env` call; `read_oauth_token()`
(`fworker.py:1046-1053`) opens `OAUTH_TOKEN_FILE` and returns its contents with no
module-level caching — its own docstring: "read fresh per attempt, never cached in a
module global". One `claude-max` task actually ran end-to-end in this run
(`T-01`/`T-03` in `p1-exit_claudemax.json`: `model=claude-sonnet-5`, `status=completed`),
so this path was exercised for real, not just read.

## What Phase-2 must not assume

- Only `core` is conformant/claimed. `extended` (session listing, file input, artifacts,
  download headers, path-traversal probes) and `full` (harness CRUD, unsupported-model
  refusal, reserved-field variants) are unimplemented — `despag/app.py:367-379`
  capability flags are the source of truth, not this report. wq#1883 (Phase-2 wiring
  spec, `deps=1882`) already scopes the out-of-scope list; this run adds no new items to
  it.
- The `claude` binary path is environment-dependent — Phase-2's deploy spec (wq#1883
  item 1, systemd unit) must pin `DESPAG_CLAUDE_BIN` or a `PATH` that includes
  `/home/lumen/.npm-global/bin` explicitly; do not rely on the ambient shell PATH
  a BUILD-worker session happens to have.
- `wq#1885` should be re-run against a working `DESPAG_CLAUDE_BIN` before any admission-
  gate code change is made against it — see above.
- The kb `ISSUE-LOG.md` append doctrine step failed under the current worker sandbox
  (primary-tree write guard, wq#1719/wq#900): a BUILD worker cannot append to
  `/home/lumen/kb/ISSUE-LOG.md` directly, only via a dedicated kb worktree + `lumen-pr`,
  which no despag ticket's `needs` currently provisions. Same structural class as
  ISSUE-LOG #361. Recorded here instead; entry #374 text is in this ticket's `wq done`
  notes for manual porting.
