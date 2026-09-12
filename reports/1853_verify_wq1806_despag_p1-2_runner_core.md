# wq#1853 — Adversarial verify-pass of wq#1806 (despag P1-2: runner core — one `claude -p` per turn, canonical)

**Verdict: CONTRADICT** — the runner internals shipped by `a56b956 feat(despag): wq1806
p1-2 runner core` (PR #527, branch `feat/wq1806-despag-p1-2-runner-core-one-claude-p-per-a1`)
are solid and match their own tests (68/68 pytest reproduced live, including all 7 new
runner tests). But the PR branches off `4230e60` (wq#1805, the commit *before* wq#1807
landed on `main`) and never rebased. Its rewritten `despag/app.py:create_response` silently
has no path back to the `Idempotency-Key` admission and per-session lease fencing that
wq#1807 already shipped to `main` in the same function — a defect only visible by diffing
against current `main`, not against the PR's own merge-base, and not caught by any test in
either PR because wq#1807's fencing tests live in `test_control.py` (unit tests of
`ControlStore` itself) and were never re-pointed at the new `create_response`.

## What was shipped

`despag/runner/turn.py` (`Turn`, `TurnRegistry`): one `claude -p` subprocess per turn,
`start_new_session=True` + `os.killpg(..., SIGKILL)` on cancel/cap/hang (reaches Bash-tool
grandchildren), wall-clock cap via `threading.Timer`, frame-gap inactivity watchdog,
cancel-before-spawn race handled, `_release_proc` closes all three pipes in `finally`,
`evict_finished` drops old done records. `despag/runner/canonical.py`: canonical schema
*is* Claude Code `stream-json`; `_strip_claude_error_text` drops CLI retry-banner text
blocks and rewrites a no-real-answer result to `subtype=error`; `_norm_token_usage` picks
FRESH input tokens across field-name variants. `despag/backends/claude_max.py`: thin
wrapper that imports (never copies) `fworker.build_env`/`fworker.build_claude_cmd`.
`despag/app.py`: wires `POST /v1/responses` (blocking + `background:true`+polling,
`stream:true` still 501), `GET /v1/responses/{id}`, `POST /v1/responses/{id}/cancel`.
Tests: `despag/tests/fake_backend.py` (a controllable stream-json emitter, never spawns
real `claude -p`) + `despag/tests/test_runner.py` (7 cases: killpg-reaches-grandchild,
wall-clock timeout, frame-gap hang, API-error scrub, fd-count-stable-across-50-turns,
cancel-before-spawn race, evict-finished). Deliverable `despag/reports/p1-2_runner.json`
(uncommitted, in the shared `/home/lumen/foreman` checkout): UHP conformance class `core`,
2/2 checks pass (C-02 cancel-terminal-is-noop, C-03 cancel-running-ends-non-running).

## Method — ran it, not just read it

- Materialized the PR's `despag/` tree into this worktree (`git checkout
  origin/wq1806-review -- despag/ evidence.json`, main untouched) and ran
  `python3 -m pytest despag/tests/ -v`: **68 passed**, including all 7 new
  `test_runner.py` cases. Matches wq#1806's own notes.
- Read every new/changed line in `turn.py`, `canonical.py`, `claude_max.py`, `app.py`
  against `PHASE0_WIRING_SPEC.md` C.1/C.2/E.1/E.2/E.7 and against `fworker.py`'s actual
  `build_env`/`build_claude_cmd` signatures (`harness/fworker.py:1059,1143`) to confirm
  `claude_max.build_turn` imports rather than reimplements them (A.4) — confirmed, exact
  match.
- **Could not** re-run the official `uhp_conformance` CLI acceptance probe the ticket
  names: `needs` on wq#1853 is only `ssh(anubis)` (capability_lint), while wq#1806's own
  `needs` explicitly listed `dir(/home/lumen/research/harnessrouter-ref)` +
  `.../src/protocol/conformance`, which this verify ticket did not inherit — confirmed by
  a direct `ls` attempt, refused ("may only list files in the allowed working
  directories"). Same structural gap as ISSUE-LOG #361 (that entry was itself filed by
  wq#1850, the verify-pass for wq#1805 — this is the **second** recurrence against the
  same despag PR chain). Did not retry ssh (wq#1107 loop-breaker lesson). Logged as
  ISSUE-LOG #362 below. Treated the committed `p1-2_runner.json` as evidence of what the
  real suite found and could not independently re-verify it beyond reading it — it is
  internally consistent with the shipped `Turn.cancel()`/`_kill()` behavior.
- Checked whether the PR is actually mergeable onto current `main` (it is the thing that
  will actually ship, not the PR's own stale diff): `git merge-tree $(git merge-base
  origin/main origin/wq1806-review) origin/main origin/wq1806-review` — **6 conflict
  hunks**, 3 of them in `despag/app.py` at exactly the `create_response`/imports/module
  docstring region (see Finding 1).
- Reproduced a falsy-zero bug directly in a Python shell against the materialized `Turn`
  class (Finding 2).

## Claim-by-claim check

1. **FINDING (primary) — the shipped `create_response` has no path to the
   `Idempotency-Key` admission or per-session lease fencing wq#1807 already put in this
   exact function on `main`.** `despag/app.py` on `main` (post-`2a7c136`, pre-existing
   before this PR was authored) already has: `CONTROL = ControlStore(...)` (`app.py:79`),
   `_idem_admit()` (`app.py:231-248`, reserves an `Idempotency-Key` for a response id,
   replays a prior winner instead of double-spawning), and a per-session lease
   (`CONTROL.lease_admit(session_id, resp_id, ttl_s=SESSION_LEASE_TTL_S,
   reject_on_conflict=True)`, `app.py:285`) that answers `409 session_busy` when a second
   request targets a session another request already holds. wq#1806 branched from
   `4230e60` — the commit *before* `2a7c136` (wq#1807) landed this — and its
   `create_response` (`despag/app.py:358-402` on the PR branch) is a full rewrite with no
   `CONTROL` import, no `_idem_admit`, no `lease_admit`, and `_resolve_session` (`app.py:
   155-171` on the PR branch) is unlocked plain-dict read/write with no admission check
   at all. Reproduced concretely with `git merge-tree`:
   ```
   $ git merge-tree $(git merge-base origin/main origin/wq1806-review) \
       origin/main origin/wq1806-review
   changed in both
     despag/app.py
   <<<<<<< .our      (main, has CONTROL/_idem_admit/lease_admit)
   ... from despag.backing.control_sqlite import ControlStore
   =======            (wq1806, has none of it)
   ... from despag.backends import claude_max
   ... from despag.runner import canonical
   ... from despag.runner.turn import TurnRegistry
   >>>>>>> .their
   ```
   (3 conflict hunks land inside `despag/app.py`, exactly spanning the imports, the
   module docstring, and the whole body of `create_response`.) This is not a
   hypothetical: the PR as filed **does not merge onto `main` today**. Whoever resolves
   that conflict has to manually re-integrate wq#1807's admission logic into the new
   `Turn`-based `create_response` — nothing in this PR does that, nothing in its tests
   would catch the omission (wq#1807's own `test_control.py` tests `ControlStore` in
   isolation, not `create_response`), and the PR text never mentions the collision. If
   the conflict is resolved by favoring the new function body wholesale (the natural
   read of "wq1806 is newer, keep its create_response"), the safety net silently
   disappears: a client retrying a request with the same `Idempotency-Key` after a
   timeout gets a **second independent turn** instead of a replay of the first, and two
   concurrent requests against the same session get **two `claude -p --resume
   <cli_session_id>` processes racing in the same session workspace directory** with no
   `409 session_busy` rejection — exactly the failure mode wq#1807 was built to close.

2. **FINDING (minor) — `Turn.__init__` treats `timeout_seconds=0` as "unset," silently
   giving the caller the full 6-hour default instead of an immediate/near-zero cap**
   (`despag/runner/turn.py:73`): `self.wall_clock_s = min(timeout_seconds, max_turn_cap)
   if timeout_seconds else max_turn_cap` — `0` is falsy in Python, so `if timeout_seconds`
   is `False` for an explicit `0`, same as `None`. Reproduced live:
   ```
   >>> Turn("t", ["true"], ".", {}, timeout_seconds=0, frame_gap_s=30).wall_clock_s
   21600
   ```
   `models.CreateResponseRequest.timeout_seconds: Optional[int] = None` does not forbid
   `0`, so this is reachable from `POST /v1/responses`. Low severity (a caller asking for
   a 0-second budget is an unusual request), but silent — the caller gets no error and no
   indication its cap was ignored.

3. **Noted, not escalated — `TurnRegistry.evict_finished` is never called by anything in
   the shipped app.** The module's own docstring (`turn.py:5-7`) frames it as delivering
   "the same finished-record eviction HR needed," and `test_evict_finished_drops_old_
   done_records` proves the method itself works, but `despag/app.py` never schedules it
   (no periodic task, no call on request handling) — `RESPONSES`, `SESSIONS`, and
   `REGISTRY._turns` all grow unbounded for the life of the process in this build. Not
   treated as a hard finding because fd hygiene (E.7's actual measured failure mode) is
   independently handled per-turn by `_release_proc`, and wiring a periodic sweep into
   the FastAPI app may be deliberately deferred — but it's worth flagging since the
   docstring reads as a completed claim.

## Adversarial angles tried, nothing found

- `_kill()`'s `ProcessLookupError`/`PermissionError` fallback to `proc.kill()` — correct,
  and `test_killpg_kills_the_grandchild` proves the group-kill path actually reaches a
  grandchild the `Turn` never spawned directly.
- Cancel-after-completion no-op (`cancel()` checks `not self.done.is_set()`) — a finished
  turn's status can't be flipped by a late cancel; matches sessions.md §4's "cancelling a
  terminal task ... changes nothing" (C-02, and the shipped conformance report agrees).
- `_strip_claude_error_text` / result-frame rewrite for a no-real-answer turn — read
  line-by-line against HR's own three-weeks-of-evidence rationale (E.2); the empty-`kept`
  vs partial-`kept` vs untouched branches are all correct, and `test_api_error_scrub_
  rewrites_result` exercises the exact "only the error line" case.
- fd hygiene: `test_fd_count_stable_across_50_turns` (`<=3` growth over 50 turns) passed
  live in this sandbox, not just claimed.
- `claude_max.build_turn`'s argument mapping onto `fworker.build_env`/`build_claude_cmd`
  — checked against the real signatures (`harness/fworker.py:1059,1143`); every keyword
  lines up, nothing is silently dropped or reordered.
- Exit-code normalization for a signal-killed child (`128 - self.exit_code` for negative
  `returncode`) — correct POSIX convention, matches `fworker.py:1334-1340`'s own note.

## Bottom line

CONTRADICT. The runner core itself (`turn.py`/`canonical.py`/`claude_max.py`) is
well-built and its own tests are honest and reproduced live (68/68). But the PR was
authored against a stale base and its `app.py` rewrite has no path back to wq#1807's
already-shipped Idempotency-Key admission and per-session lease fencing — confirmed by a
literal merge-tree conflict against current `main`, not a hypothetical. That is a
concurrency-safety regression risk if this PR lands without deliberately re-integrating
`CONTROL`/`_idem_admit`/`lease_admit` into the new `create_response`. Also found a minor
falsy-zero `timeout_seconds` bug (Finding 2). None re-implemented here per the
verify-pass archetype — filed as a follow-up fix ticket (`claude-opus-5@high`, deps
wq#1806 + wq#1807) per this archetype's escalation rule.

## ISSUE-LOG

Appended #362 (see `/home/lumen/kb/ISSUE-LOG.md`): second recurrence of #361's class —
this verify ticket's `needs` (`ssh(anubis)` only, from capability_lint) did not inherit
wq#1806's own `needs` (`dir(/home/lumen/research/harnessrouter-ref)` + subpaths), so the
official `uhp_conformance` acceptance probe could not be re-run.
