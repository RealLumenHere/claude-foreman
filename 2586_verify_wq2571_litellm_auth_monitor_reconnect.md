# wq#2586 — verify of wq#2571 (PR#964, commit 97b86b6, claude-foreman repo)

Verifier: claude-sonnet-5@high, fresh instance (same model as author). Verdict:
**CONFIRM**. No contradiction found; no escalation.

Attempt 3 of this ticket (attempts 1-2 were both KILLED before reaching a verdict —
both died on `bash /home/lumen/foreman/ops/.../install.sh --verify` and
`.../test_litellm_auth_monitor.sh` invoked by absolute path, neither of which matches
the sandbox's `Bash(bash scripts/*)` allowlist entry. Not retried this run; worked
around per ISSUE-LOG #712/#713 (below).

## Per claim

| wq#2571 claim (AC) | Result | Evidence |
|---|---|---|
| AC1: in-script reconnect loop w/ backoff, no reliance on `Restart=always` | **CONFIRMED** | `ops/wq2571-litellm-auth-monitor-reconnect/litellm-auth-monitor:109-125` — `run()` wraps `docker logs --follow --timestamps --since "$since"` in an outer `while true`; script runs under `set -uo pipefail` (no `-e`), so the inner `while read` loop ending (container stop/recreate) falls through to `sleep "$backoff"` + exponential doubling capped at `MAX_BACKOFF=60`, never exits. `MainPID` therefore never dies on a container bounce, so `systemd` has nothing to restart. |
| AC1b: resumes from last-seen timestamp, not fixed `--since 1m` | **CONFIRMED** | `process_line()` (`:74-83`) captures the RFC3339Nano prefix from each `--timestamps`-prefixed line and calls `save_last_seen()` (`:64-66`, atomic write-then-rename to `STATEFILE`); `run()`'s `since=$(last_seen_ts)` (`:112`, `:56-62`) reads it back on every reconnect, falling back to "1 minute ago" only when `STATEFILE` is empty (first run). |
| AC2: JSON built with a real encoder, backslash-safe | **CONFIRMED** | `emit_json()` (`:44-54`) passes every field through the environment (`EJ_*` vars, never string-interpolated into the python source) into a `python3 -c`-equivalent heredoc that calls `json.dumps`. |
| AC3: NRestarts flat across a container restart | **CONFIRMED, live** | `systemctl show lumen-litellm-auth-monitor.service -p ActiveState,NRestarts,MainPID` → `ActiveState=active`, `NRestarts=0`, live at time of verify. `journalctl -u lumen-litellm-auth-monitor.service` shows the old churn (`restart counter is at 153`, then `154`) stopping exactly at the wq#2571 deploy's manual restart (`04:20:37`, which itself resets the counter) — zero automatic restarts since. Could not force a live container bounce to watch `NRestarts` a second time (`docker` is restricted to `ps`/`logs` in this sandbox, and even `docker ps` is denied here — ISSUE-LOG #713); relying on the structural argument (no `-e`, reconnect loop never exits) plus the absence of any restart in the post-deploy journal window, same limitation the PR's own README documents for its own verification. |
| AC4: test exercises a backslash-containing line | **CONFIRMED, independently re-run** | `bash scripts/wq2571_test_litellm_auth_monitor.sh` (allowlisted wrapper around `ops/.../test_litellm_auth_monitor.sh`) → `PASS: backslash log line -> valid JSONL`, `PASS: last-seen timestamp persisted for resume`, `PASS: stale 'reconnects automatically' claim removed`, `ALL PASS`. |
| Deploy: live script matches bundle, stale claim removed | **CONFIRMED** | `diff` between `origin/main:ops/wq2571-litellm-auth-monitor-reconnect/litellm-auth-monitor` and the live `/home/lumen/bin/litellm-auth-monitor` — byte-identical. `grep -q 'reconnects automatically' /home/lumen/bin/litellm-auth-monitor` → exit 1 (absent). This manually reproduces both checks `install.sh --verify` would run (`vfile` + the `_vmiss` grep), since the script itself couldn't be invoked directly (ISSUE-LOG #712). |
| wq#2571's own ticket liveness probe (`grep -q 'reconnects automatically' ... exit 0 = unfixed`) | **CONFIRMED fixed** | Same grep above, exit 1 on the live target. |

## Attempts to break the fix that did NOT find a defect

- Reconnect after a stream error (docker daemon hiccup, container not found): stderr is
  merged into the followed stream (`2>&1` on the process substitution, `:116`), so an
  error message is just fed to `process_line()` as an unmatched line (harmless) and the
  loop still falls through to backoff/reconnect — no special-case needed, no crash path.
- `since=$(last_seen_ts)` on reconnect uses a timestamp equal to the last line actually
  processed; `docker logs --since <exact-ts>` could in principle re-deliver that same
  line once more on reconnect (duplicate JSONL row), but this is a duplicate-emission
  risk, not the data-loss risk AC1b exists to close — strictly better than the original
  bug (missed events), so not a blocking finding.
- `backoff` resets to `1` on every successfully read line (`:114`), not just on
  reconnect — verified this still produces the intended 1/2/4/.../60s growth only when
  the stream produces zero lines before ending (immediate docker failure), matching the
  design in the PR's README.
- Offline test's fd-3 wiring (`process_line` reading a "Requester IP Address:"
  follow-up line from fd 3) is unchanged, pre-existing logic per the PR's own README —
  out of scope for this diff, not re-litigated here.

## Actions taken

| Action | Rationale |
|---|---|
| ISSUE-LOG **#712** appended | Records that `bash <absolute-path>/install.sh --verify` is structurally unreachable from this sandbox (not on any allowlist entry), and the workaround (manually replicate `vfile`/grep checks with allowlisted `diff`/`grep`) |
| ISSUE-LOG **#713** appended | Records `docker ps` being denied despite matching the `Bash(docker ps*)` allowlist entry, and `guard.sh` blocking unrelated commands that merely contain the substring `docker` (e.g. `journalctl -u docker`) |
| wq#2571 left `done`/PR#964 left merged, not reopened or edited here | Verify-only per archetype 8 — no contradiction, nothing to escalate |
