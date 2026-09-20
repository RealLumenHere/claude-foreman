## What

Finishes wq#1095: removes `probe_a5_health()` and its `A5_SERVICES` / `A5_STATE` / threshold constants from the live `/home/lumen/sentinel/probes/sentinel_probes.py` (non-git), leaving a 3-line comment pointing at wq#1064 / #1092 / #1095. The repo carries the diff, a README, and an updated wq#1092 test.

## Why

wq#1095 only commented out the `probe-a5-health` PROBES tuple. Its own direction said to remove the code, and its proof step (grep the live file for `lumen-planner|generator|evaluator`, non-comment) returned 1 hit (`A5_SERVICES`, :740) instead of 0. The a5 trio has been retired since 2026-08-21; the stale-target guard lives on in `_stale_or_none`, so the dead body has no remaining role. Chose option (a) of the ticket, finishing it literally.

## How

- Live file: 48-line body plus constants replaced by a comment; PROBES comment no longer claims the body is "left intact". Diff in `ops/wq1122-a5-health-code-removal/sentinel_probes.diff`.
- `state.json`: the `_stale_probe_tickets` key was already absent, so nothing to delete.
- `ops/wq1092-probe-target-liveness-guard/test_probe_target_liveness.py`: the scenario that called `probe_a5_health()` now asserts it, `A5_SERVICES` and the PROBES entry are gone. Added a `SENTINEL_PROBES_PATH` override because the default relative path does not resolve from the repo.

## Testing

- Live file parses (`ast.parse`); the wq#1095 adversarial grep now returns 0 non-comment hits.
- The new test scenario fails on the pre-change file and passes on the live one.
- The `[state-drift]` scenario fails both before and after (hathor/isis offline in tailscale); unrelated to this change.

## Risk / rollback

Low: the probe was already unregistered and never ran. Rollback: copy `sentinel_probes.py.bak.20260920_pre_wq1122` (same dir as the live file) back over `sentinel_probes.py`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
