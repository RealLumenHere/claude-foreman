# wq#5317 — verify of wq#5275 (PR 2743, 148bf837): wq394-netdata-canary --verify

Verdict: **CONFIRMED.** Probe: `python3 reports/wq5317_verify_wq5275_probe.py` → `RESULT: CONFIRMED`, exit 0.

## What the PR claims
`ops/wq394-netdata-canary/install.sh` --verify asserted enabled/active (and the sampler
payload) on units the 2026-10-08 subtraction (1G) disabled on purpose, so it could never
pass. The fix gates those checks on `systemctl is-enabled` (install.sh:28-47), keeps the
unit-file checks unconditional (install.sh:16-19), makes disabled-but-active a MISS, and
makes the install arm refuse unless `WQ394_REENABLE=1` (install.sh:53-58).

## Attempts to break it (all live on lumen, 2026-11-22)
| # | Attack | Result |
|---|---|---|
| 1 | Harness probe `liveness.ops_verified('wq394-netdata-canary')` | SATISFIED, `VERIFY OK` via both OFF lines |
| 1b | Is the live state actually intended-off, or did the gate just hide a problem? | `systemctl is-enabled/is-active`: timer and netdata.service both `disabled`/`inactive` |
| 2 | Append a line to the bundle's `.service` / `.timer` (scratch copy) | STILL_BLOCKED both times: unit-file drift is still caught while off |
| 3 | Force the timer gate open (`if true`) in a scratch copy | STILL_BLOCKED: not enabled / not active / not scheduled + payload differs. The gate hides real state and the check can still fail |
| 3b | Force the netdata gate open | STILL_BLOCKED: `not active: netdata.service` |
| 4 | Run the install arm without opt-in (curl/cp/sed/mkdir/systemctl shimmed) | rc=2, refusal on stderr, no shim touched. With `WQ394_REENABLE=1`: reaches curl (rc=97) |
| 5 | Did the author make up "intended-off"? | The parent revision (148bf837^) install.sh:24-26 already recorded it (wq#4910, written before this PR); ISSUE-LOG 1728 cites REPORT.md:48. Same `scratch/subtraction/REPORT.md` is the source of the `lumen.vlogs_forwarder` lumen_state fact |
| 6 | PR's own tests `ops/tests/test_wq5275_wq394_verify.py` | 9 passed |

## Non-blocking notes
- The test shim's `is-enabled` exits 0 only for `enabled`. Real systemctl also exits 0 for
  static/indirect/generated/alias. This doesn't matter here: the timer has an `[Install]`
  section, and netdata.service is a packaged unit that installs as enabled.
- Nothing checks a disabled timer whose oneshot `wq394-netdata-sampler.service` was started
  by hand. Small gap, and it is the same as before the PR.
- The hand-patched sampler.py/analyze.py drift (ops/DEPLOY_TRIAGE.md:102) is now not
  reported while the units are off. It comes back as soon as someone enables the timer,
  and `WQ394_REENABLE=1` install copies the files again. This matches the intent.
- `liveness.ops_verified` keeps only the last 200 chars, which cut the head of the MISS
  line in attack 2 (ISSUE-LOG 1738). This is a probe-writing hazard, not a PR defect.
- Commit 148bf837 has a `Co-authored-by: Lumen Industries` trailer, which house style
  forbids. It looks like it came from the commit tooling, not the diff. Not a correctness issue.
