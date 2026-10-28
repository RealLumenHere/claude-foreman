# wq#4761 — cross-repo evidence note (same shape as wq#4711/wq#3487/wq#2573/wq#3315)

The dispatcher checked this ticket out as a **foreman** worktree, but the deliverable — the
`grant_requests` table, the `awaiting_grant` ticket status, and the `wq await-grant` /
`wq grant approve|deny|list|request|consume|show` verbs — lives in `wq`, which is in the
**bin** (`lumen-bin`) repo. Applied the established workaround: nested a real bin worktree
under this worktree's gitignored `_probe/` prefix
(`git -C /home/lumen/bin worktree add /home/lumen/worktrees/wq4761-a1/_probe/bin-wq4761 -b
feat/wq4761-grant-requests main`), built and tested the change there, landed it via
`lumen-pr` from inside that nested tree, then removed the nested worktree. This note is the
pointer so a citation can resolve against *this* branch's diff while the real bin-side change
is documented and quoted here.

## bin repo change (landed, not part of this branch's diff)

- PR: http://100.121.17.5:3000/lumen/lumen-bin/pulls/282 — merged as `b5bcf66757` (squash)
  from branch `feat/wq4761-grant-requests`; primary `/home/lumen/bin` fast-forwarded to
  `24a9824` by `lumen-pr land`, so the live `wq` already serves the feature
  (`/home/lumen/bin/wq grant list` → `0 grant request(s)`).
- Files: `wq` (+304/-5), `tests/test_wq4761_grant_requests.py` (new, 313 lines, 19 tests).

## What landed (DESIGN.md §2.3 steps 3-5, §2.6, §2.7 F4/F5/F8)

Schema, in `init()` (wq @24a9824, the `wq#4761` block after the `retired_sessions` index):

    CREATE TABLE IF NOT EXISTS grant_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL,
        session_id TEXT, tool_use_id TEXT, class TEXT, target TEXT,
        fingerprint TEXT UNIQUE NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
        requested_at TEXT, decided_by TEXT, decided_at TEXT, reason TEXT,
        expires_at TEXT, consumed_at TEXT, consumed_lease TEXT)

plus additive `tasks` columns `resume_session` and `grant_req`, and
`GRANT_TTL_HOURS = _envint("WQ_GRANT_TTL_HOURS", 24)` for the default 24h expiry of both a
pending request and an approval. Deliberately NOT a reuse of `wq hold` (soft intent marker,
wq#1461), per the ticket.

Behavior (all in `wq` @24a9824, section marker `wq#4761 hold-state grants` above
`cmd_preflight`):

- `wq grant request <ticket> --fingerprint <64-hex sha256>` files `state=pending`;
  idempotent on the UNIQUE fingerprint (duplicate → existing live row, terminal → refused).
- `wq await-grant <ticket> --req <id> --session <sid>` parks a CLAIMED ticket:
  owner_ok + fence-guarded, releases subsystem locks and the lease,
  `status='awaiting_grant'`, stores `resume_session`/`grant_req`. The dispatcher's lane scan
  selects only `status='open'`, and `claim --id` refuses awaiting_grant with an explicit
  `AWAITING_GRANT` message — never claimable while parked.
- `wq grant approve <req>`: refuses **self-approval** (F8) when the caller or its registered
  base matches the request's `session_id`, that session's base, or the ticket's stable
  `fworker-wq<N>` identity — exit 1, "REFUSED: self-approval …". Otherwise
  `state='approved'` with `decided_by`/`decided_at` and a fresh TTL, and the parked ticket
  returns to `open` with `resume_session` kept for the `--resume <sid>` spawn.
- `wq grant deny <req> --reason "…"`: `state='denied'`; the parked ticket goes `blocked`
  with `block_reason='grant denied: <reason>'`.
- `wq grant consume --fingerprint <fp> [--lease <id>]`: the atomic consume API for
  hold_gate (F5) — one `UPDATE … SET state='consumed', consumed_at=?, consumed_lease=?
  WHERE fingerprint=? AND state='approved' AND consumed_at IS NULL AND (expires_at IS NULL
  OR expires_at >= ?)` under `BEGIN IMMEDIATE`; rowcount 1 wins, the loser gets a distinct
  exit code (1 unknown / 2 not-approved / 3 expired / 4 already consumed).
- `wq reap` additionally expires stale pending/approved requests past `expires_at` and
  blocks tickets still parked on them (F4), honoring `--dry-run`.
- F7: `wq release` / `wq done` clear `resume_session`/`grant_req` so a fresh attempt never
  inherits a grant.

## Tests (bin repo, `tests/test_wq4761_grant_requests.py`)

19 subprocess tests against a scratch `WQ_DB`, covering every transition
(pending→approved/denied/expired/consumed), park + unclaimability (lane scan AND
`claim --id`), self-approval refusal (requesting `fworker-wq<N>` identity → rc!=0,
"self-approval"), deny-reason requirement, re-decide refusal, reap expiry + `--dry-run`
no-mutation, one-shot consume, and a **double-consume race**: two concurrent
`wq grant consume` processes on one approval, asserting exactly one exit 0.

Run in the nested worktree pre-land: `pytest tests/test_wq4761_grant_requests.py -q` →
**19 passed**. Regression batch (test_wq_reap, test_wq_lease_fence, test_wq_reopened,
test_wq4659_add_claim, test_wq4113_dep_id_guard, test_wq_show_json, test_wq_needs) →
**60 passed**. The 10 failures in test_wq_done_gate_terms/test_wq_hygiene_reconcile fail
identically on unmodified main (verified by running them in `/home/lumen/bin` at `08a45a6`
before the merge) — pre-existing, unrelated.

Re-runnable probe, pinned to the merged rev:
`git -C /home/lumen/bin show 24a9824:tests/test_wq4761_grant_requests.py | grep -c "^def test_"`
→ 19.
