# wq#3137 — verify wq#3122 (LiteLLM->Langfuse trace-rate floor monitor)

**Verdict: CONFIRMED**

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1414 (merged 50503871a1,
commit `a2c20e1d` on main). Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance), archetype 8 adversarial verify-pass.

## What was shipped

`harness/langfuse_trace_rate_monitor.py` + `harness/tests/test_langfuse_trace_rate_monitor.py`
+ `ops/wq3122-litellm-langfuse-trace-drop/{README.md,install.sh,*.service,*.timer}`.
Polls Langfuse's `/api/public/traces?fromTimestamp=...` for `meta.totalItems` in a
rolling window (default 3600s) and alerts via `ntfy-send` if the count is at/under a
floor (default 66) matching the 11-66/hr degraded range wq#1555/wq#3122 measured.
This is a **detection** tool, not a fix — the PR and wq#3122's own README are explicit
that the actual root cause (litellm's Langfuse callback silently swallowing the real
ingestion exception behind `log_level=WARNING`) cannot be fixed from a build-worker
sandbox (no docker access, compose file outside `--add-dir`, restart forbidden during
a concurrent litellm-upgrade harness run). That gap is tracked separately at
**wq#3130** (status: blocked, unclaimed, correctly scoped to an orchestrator-privileged
maintenance window) rather than silently dropped — consistent with the full-autonomy
doctrine of shipping the reachable slice and queuing the physical-access-gated
remainder instead of gating on it.

## Adversarial checks performed

1. **Read the diff cold** (`git show a2c20e1d`) — new files only, no edits to existing
   logic, low blast radius.
2. **Ran the unit suite fresh**: `python3 -m pytest harness/tests/test_langfuse_trace_rate_monitor.py -q`
   → **10 passed**. Covers `under_floor` boundary (at-floor, below, above, `None`),
   `run()`'s skip-on-no-creds / skip-on-fetch-failure / alert-only-under-floor paths,
   and `fetch_trace_count`'s JSON parse + transport-error handling.
3. **Checked the code path for the classic wq#2290-style probe-can't-fail trap**: the
   ticket's acceptance risk here is "does the monitor actually reach the real host,"
   not just the mocked unit tests. Confirmed via **live evidence outside the mocked
   test population**:
   - `systemctl is-enabled langfuse-trace-rate-monitor.timer` → `enabled`;
     `is-active` → `active`.
   - `journalctl -u langfuse-trace-rate-monitor`: one real pass at 2026-09-29T05:53:12Z,
     `ntfy-send: delivered [primary=200 oob=200]` — the monitor made a real HTTP call
     to the live Langfuse host with real credentials, parsed a real response, decided
     the count was at/under the floor, and successfully delivered a real alert on both
     ntfy channels. This is a genuine end-to-end run against production, not a
     same-population mock re-check.
   - Cross-referenced against wq#3122's own status: the underlying litellm callback
     bug is still open (wq#3130 blocked/unclaimed, no restart has happened since the
     2026-09-28T10:25:19Z regression). So an immediate alert on the monitor's first
     real pass is the **expected true-positive** outcome, not a false alarm — the
     degraded 11-66/hr floor is presumably still in effect.
4. **Tried to break the floor threshold**: wq#3122's own measured data has the
   healthy range (60-630/hr) and the degraded range (11-66/hr) *overlapping* at
   60-66/hr. A floor of 66 with `<=` comparison means a genuinely healthy but
   low-traffic hour in that 60-66 band would also trigger a false alert. This is a
   real, reproducible edge case (confirmed by re-reading the ticket's own detail text,
   not guessed) — but it is a **known, explicitly-documented tradeoff**, not an
   oversight: both the module docstring and the README state the overlapping ranges
   plainly and pick the conservative side (catch all degraded hours, accept some
   false positives in the ambiguous band) rather than the alternative (a lower floor
   that guarantees no false positives but would miss real degraded hours that also
   land in 60-66). Given the failure mode this ticket exists to close was a 30-hour
   *silent* blind spot, erring toward over-alerting over under-alerting is the
   defensible choice. Flagging as a noted tuning caveat, not a defect.
5. **Checked the systemd contract**: `run()` returns 1 on deliberate skip (no creds /
   fetch failure) and 0 otherwise; `.service` sets `SuccessExitStatus=0 1` — matches.
   Timer's `OnBootSec=5min` / `OnUnitActiveSec=15min` / `RandomizedDelaySec=2min` are
   sane for a 1-hour rolling window (several samples per hour without single-sample
   paging).
6. **Checked `install.sh --verify`** matches what's actually on the host: unit files
   installed, timer enabled and active — all independently reconfirmed live above,
   not just trusted from the ticket's own notes.

## Things NOT independently re-verified

- The exact `langfuse` 2.57.13a0 API client shape (`meta.totalItems`,
  `fromTimestamp` query param) was not re-derived from a locally pulled wheel — no
  network egress / `pip download` available in this worktree, and `langfuse` isn't
  installed in this Python env to inspect directly. This is accepted on the strength
  of (a) the real live run above returning a 200 and completing a real alert (proves
  the shape is right against the actual production endpoint, which is stronger
  evidence than reading the SDK source), and (b) the same claim being independently
  restated with consistent file:line detail in wq#3130's ticket body.

## Conclusion

The shipped diff does what it claims: a tested, installed, and now-running trace-rate
floor monitor that closes the "nothing watches the rate, only the export lag"
blind spot wq#1555 exposed. It does not and does not claim to fix the underlying
litellm/Langfuse callback bug — that remainder is honestly tracked at wq#3130, not
buried. No correctness defect found; one tuning caveat (floor/healthy-range overlap)
noted but judged an intentional, defensible tradeoff rather than a bug. **CONFIRM.**
