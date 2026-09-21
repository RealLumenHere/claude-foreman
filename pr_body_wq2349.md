## What

Give the SessionEnd token revocation its own broker grant, so `guard.sh --lifecycle end` can
actually call the token-engine `ticket-end` verb instead of being denied.

## Why

That call has been made since wq#2277 and has never once been allowed: `events/fleet-exec.jsonl`
holds ~990 of them and ~990 denials, almost all `grant:grant_missing_env`, zero `allow`. So
TOKEN_ENGINE_DESIGN.md §3.4 revoke-at-session-end had never run — every minted provider token lived
to its natural expiry, and the daemon-start reaper was the only thing that had ever revoked one.

Root cause: `harness/guard.sh:53` invoked `te_client --verb ticket-end` with `--ticket`/`--worker`
only, so `te_client.py:45-46` fell back to `FOREMAN_GRANT_FILE`/`_SHA` — the **exec** grant, which is
the wrong grant twice over:

- `fworker.resolve_exec_grant` mints only for a ticket carrying `needs:ssh/copy/restart` tags, and
  almost none do, so the vars are unset → `fleet_exec_policy.py:82` `grant_missing_env`;
- even when it exists it holds ssh/copy/restart entries, never `{kind: token_engine, node: daemon}`
  → `grant_no_match` (the one such line in the corpus, wq#2273 attempt 3's PR-minting run).

The daemon was never the problem: every call reached the grant check, so the deployed broker has
known the verb since T9.

## How

- `fworker.write_ticket_end_grant` writes a one-entry `{token_engine, daemon}` grant to
  `_runs/wq<id>/ticket-end-grant-a<n>.json` (0444 — the daemon re-reads and re-hashes it as uid
  tokeneng) for **every attempt, independent of needs tags**, and exports
  `FOREMAN_TICKET_END_GRANT_FILE`/`_SHA`. One file per attempt: attempt 1's SessionEnd hook can fire
  after attempt 2 starts, and a shared path would leave it holding a sha of rewritten bytes.
- Lease is wall clock + 30 min, because SessionEnd fires *after* the child exits — at the ceiling
  itself for a watchdog kill.
- `guard.sh` presents that pair explicitly, and **skips the broker when it is absent**
  (`ticket_end_skip_no_grant`) instead of spending a round trip that can only earn a deny.
- Authority is self-scoped: `load_grant` forces `grant.ticket == req.ticket` and the worker id is
  bound, so the verb can only revoke what this attempt minted. Writing the grant is best-effort — a
  failure costs a revoke (the reaper still sweeps), never a dispatch.

No daemon-side change.

## Testing

- Live, against the **deployed** broker: `fleet-exec.jsonl` ts=1789961745 →
  `{"verb": "ticket-end", "decision": "allow", "rule": "grant:token_engine", "ticket": "2349",
  "worker": "fworker-wq2349-a2", "exit": 0}` — the first `allow` for the verb in the file's history.
  Result frame `{"had_entries": 0, "revoked": 0, "failed": 0}`: this ticket minted nothing, but the
  verb *ran*, which is what had never happened.
- `harness/tests/test_guard.py` 51 pass, including four new fake-broker tests: the frame carries the
  ticket-end grant; the missing-grant path sends **zero** bytes to the socket; the exec grant is not
  borrowed as a fallback; a denial still never blocks session end.
- `harness/tests/test_fworker_grant.py` 20 pass, including the grant verifying through the broker's
  own `fleet_exec_policy.load_grant`, its ticket/worker/sha binding, the lease margin, 0444 mode and
  per-attempt paths, and a write failure not being a dispatch failure.
- `token-engine/tests` 56 pass; `python3 token-engine/tests/run_replay.py t9` → `GATE_REPLAY_T9_PASS`.
- Full `harness/tests`: 1705 pass, 6 fail — all 6 pre-existing, reproduced with these two files
  reverted to their parent commit (`test_liveness_clear`, `test_liveness_ops_verified`,
  `test_memory_injection`; none touch the guard or grant path).
- Two stale tests fixed in passing: `test_grant_file_is_0400` asserted the pre-wq#2270 mode (0400 vs
  0444), and `test_lifecycle_events_logged` inherited the worker session's `FOREMAN_TICKET_ID` and
  fired a **real** ticket-end at the live broker on every run — the suite was feeding the denial
  count it was meant to describe.

## Risk / rollback

Low. The only behaviour change on the existing path is that a session without the new env vars now
skips the broker instead of calling it and being denied; nothing that previously succeeded stops
succeeding, and a failure to write the grant is logged and ignored. The new grant carries one kind
whose verbs (`ping`, `mask-selftest`, `ticket-end`) either read nothing or revoke this ticket's own
tokens. Rollback is reverting this commit: the daemon-start reaper resumes as the sole revocation
path, exactly as before.

The live SessionEnd hook runs the deployed `/home/lumen/foreman/harness/guard.sh`, so the denial
count stops growing once this lands and the primary fast-forwards.

Refs: wq#2349
