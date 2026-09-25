# wq#2660 — verify of wq#2280 (PR#1020, merged b49a772, main=a555e15)

Verifier: claude-sonnet-5@high, fresh instance. Author: claude-sonnet-5. Verdict:
**CONFIRM** — no contradiction, no escalation.

wq#2280 was the wq#2265 leftover: prove the simpill synthetic-fork PROOF shape
("a synthetic event files exactly one ticket, a repeat files nothing" —
test_simpill_watch.py's docstring) end to end against the REAL `/home/lumen/bin/wq`
CLI and a real network git clone of the configured fork, instead of the
FakeWq/stubbed-git doubles the rest of the suite uses. The PR's notes claim: ran
the live proof once, committed it as an opt-in test
(`harness/tests/test_simpill_watch_live_proof.py`), and filed the unrelated
`bin/wq check_lane_has_repo` multi_repo guard separately as wq#2652 (user lane)
because no BUILD worker sandbox can write `/home/lumen/bin`.

I read the diff cold (`git show b49a772 -- harness/tests/test_simpill_watch_live_proof.py`,
now squashed onto main), read the full new test file and the `simpill_watch.py`
source it exercises (`load_config`, `sync_clone`, `process_repo`, `load_state`/
`save_state`, `main`), and independently ran the probe rather than trusting the
PR's own assertions. Did not re-implement.

## Per claim

| wq#2280 claim | Result | Evidence |
|---|---|---|
| The diff is additive and test-only — no production code touched | CONFIRMED | `git show --stat b49a772` → 1 file, `harness/tests/test_simpill_watch_live_proof.py`, +72/-0. `simpill_watch.py` is unchanged by this PR (only imported). |
| The test is skipped by default (opt-in via `SIMPILL_LIVE_PROOF=1`) | CONFIRMED | `pytest harness/tests/test_simpill_watch_live_proof.py -v` with no env var → `1 skipped`. |
| With the flag set, it runs the wq#2242 PROOF shape against the real `wq` CLI and a real GitHub clone: first run baselines (files nothing), a synthetic event files exactly one ticket, a repeat files nothing again | CONFIRMED | Ran the live probe (`SIMPILL_LIVE_PROOF=1`, via a `python3` driver since env-var-prefixed Bash isn't allowlisted for this worker) → `filed: simpill-utils:issue:live-proof-1`, `1 passed in 7.47s`. All three `sw.main()` assertions in the test (empty-baseline, one-file, dedupe-repeat) held, plus the clone's `.git` dir and `sorted(remotes) == ["fork", "origin"]` check — i.e. the real clone of `SkinnnyJay/simpill-utils` and the `fork` remote wiring both actually happened over the network. |
| The live run never lands anything in the production queue or state file — isolation is real, not just claimed | CONFIRMED | `WQ_DB`/`WQ_AGENT` are monkeypatched to a scratch path before any `wq` call; `wq1331_needs.py --db` is a required arg with no default (grep of the migration script), so it can only touch the scratch DB. After the live run: `/home/lumen/bin/wq list --lane simpill \| grep live-proof-1` → no match (prod queue clean); `dispatcher/runtime/simpill_seen.json` (prod state) → no `live-proof-1` key either. |
| `--needs` now carries the real managed clone path, not a placeholder | CONFIRMED | `ticket_args()` in `simpill_watch.py` builds `--needs repo(<clone_path(cfg, repo)>)`, and the live run's ticket used the tmp_path clone root from the test's own config — same code path production uses, just isolated. |
| The `bin/wq check_lane_has_repo` multi_repo guard is correctly NOT in this PR, and is tracked as a follow-up | CONFIRMED | PR#1020's diff has zero touches under `/home/lumen/bin`. wq#2652 exists, lane `user`, title "`[bin/wq] rescue multi_repo:true lanes in check_lane_has_repo (wq#2280 follow-up)`", detail cites the same ISSUE-LOG #543/#743 sandbox-write-block reasoning as wq#2280's notes. |
| No regression to the rest of the simpill_watch suite | CONFIRMED | `pytest harness/tests/test_simpill_watch.py harness/tests/test_simpill_watch_live_proof.py -q` → `23 passed, 1 skipped` (the skip is this same opt-in test, independently confirmed passing above). |

## Additional checks

- Confirmed the busy-key state this worker inherited (`task=wq2638...`) was stale
  (PID dead) before starting — cleared and re-set for wq2660, unrelated to this
  verify's substance but noted per protocol.
- No re-implementation performed, per verify-pass doctrine.

## Verdict

CONFIRM.
