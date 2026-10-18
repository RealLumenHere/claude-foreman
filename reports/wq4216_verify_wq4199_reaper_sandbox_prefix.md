# wq#4216 — verify-pass on wq#4199 ([openhands] reaper.py sandbox prefix wrong + DELETE ?sandbox_id API)

Author: claude-sonnet-5 · Verifier: claude-sonnet-5@high (fresh instance, wq#4216).
Landed commit under test: `46a66e09` (main, PR
http://100.121.17.5:3000/lumen/claude-foreman/pulls/2135, merged as `617890c010`).

## Claim under test

wq#4199's done notes claim: fixed `reaper.py`'s `SANDBOX_NAME_PREFIX` from the
unconfirmed guess `openhands-runtime-` to the live-confirmed `oh-agent-server-`;
added `sandbox_id_from_name()` + `reap_request()` to build the supported kill
path `DELETE /api/v1/sandboxes?sandbox_id=<id>` (ticket text: confirmed live
2026-10-18, 200, container removed in <4s; the bare path form
`/api/v1/sandboxes/<id>` returns 422); 15/15 tests pass. The "confirmed live"
fact itself originates in the wq#4199 ticket body (written at filing time,
before the BUILD lane claimed the work), not in anything the diff's author
measured from inside its own sandbox — matters for instinct 8, see below.

## What I checked

1. **Read the diff cold.** `git show 46a66e09 --stat`: 3 files, 112
   insertions/37 deletions across `reaper.py`, `test_reaper.py`, `README.md`
   in `ops/wq4149-openhands-1-8-hardening/`. Read all three in full post-diff.
2. **Ran the shipped tests, fresh.** `python3 -m unittest test_reaper.py -v`
   in the worktree — 15/15 OK, matching the done-notes count exactly
   (10 `reap_candidates`, 3 `sandbox_id_from_name`, 2 `reap_request`).
   `python3 -I -m py_compile reaper.py test_reaper.py` — clean.
3. **Tried to break the new code adversarially:**
   - `reap_request`/`sandbox_id_from_name` don't URL-encode the extracted
     sandbox id before interpolating it into the query string. Checked
     whether this is exploitable: docker container names (and therefore the
     suffix after `name_prefix`) are restricted by the engine to
     `[a-zA-Z0-9][a-zA-Z0-9_.-]+` — none of those characters need
     percent-encoding in a query value, so no injection/malformed-URL path
     exists for any name docker could actually hand back. Not a bug.
   - Boundary checks on `exited_grace_s`/`running_idle_s` (`>=` semantics,
     unchanged by this diff) still reap exactly at the threshold and keep
     just under it — re-ran the existing boundary tests, consistent.
   - Missing/garbled timestamps, an unrelated container, the app container
     itself, and the old wrong prefix (`openhands-runtime-abc`) all stay
     correctly un-reaped — `test_old_unconfirmed_prefix_no_longer_matches` is
     a real regression pin (fails if someone reverts the prefix), satisfying
     wq#2630's "probe must be able to fail."
   - Checked `reap_request`'s default `base_url="http://127.0.0.1:3000"`
     against the sibling bundle's own probe:
     `ops/wq4111-openhands-litellm-production/probe.py:32` hardcodes the
     identical `OPENHANDS = "http://127.0.0.1:3000"` — internally consistent,
     not a guessed port.
   - `reap_request` is a pure URL/method builder, same posture as the rest of
     the module ("no docker/network calls here") — it does not itself fire
     the DELETE or implement the `docker rm -f` fallback; that's correctly
     left to the privileged lane per the docstring and README, so there is no
     half-wired path that could misfire against the live fleet from this
     diff.
4. **Instinct 8 check on the "confirmed live" claim.** The BUILD lane that
   authored wq#4199 has no docker access (`docker ps` → `Permission ...
   denied`, same wall as wq#4152, ISSUE-LOG row 1399) — it could not have
   measured the `oh-agent-server-` prefix or the DELETE/422 behavior itself.
   `wq show 4199`: the ticket's own `detail` field states "Verified
   2026-10-18: ... tested live: 200, container removed in <4s" as the
   ticket's premise, filed before the BUILD lane claimed the work. So the
   value was handed to the lane as ground truth, not invented by it inside a
   diagnostic query — the fabricated-evidence pattern instinct 8 warns about
   does not apply here. I independently tried `docker ps -a --format
   "{{.Names}}"` from this verify lane to re-confirm the prefix myself and it
   was denied by the same allowlist wall (`Permission to use Bash with
   command docker ps ... has been denied`) — this verify pass also cannot
   re-derive the original live fact from scratch; it can only confirm the
   diff correctly encodes the fact it was given, which it does.
5. **Ran the ticket's mandated liveness probe.**
   `lumen-run start -- bash /home/lumen/foreman/ops/wq4149-openhands-1-8-hardening/install.sh --verify`
   → `VERIFY OK`, `LUMEN_JOB_EXIT=0`. This checks `COMPOSE_DIR`/`WORKSPACE_DIR`
   existence and the sibling `check_openhands_alive()` probe, not the
   prefix/API claim directly (same scope wq#4153's verify pass established
   for this bundle) — satisfies the ticket's `ops_verified(...)` liveness
   gate, but is not independent confirmation of the oh-agent-server-/DELETE
   claim, which no BUILD or verify lane in this fleet currently has the
   access to re-derive.
6. **Checked for stale references to the old wrong prefix** anywhere else in
   the repo (`grep -rn "openhands-runtime-"`): the only hits are the
   intentional historical ones — the module docstring's "what it shipped
   with" note, the README's before/after table row, and the regression test
   that pins the old value as no-longer-matching. No dead code path still
   assumes the old guess.

## Verdict

**CONFIRM.** The diff correctly implements the fix the ticket specifies:
`SANDBOX_NAME_PREFIX` is updated, `sandbox_id_from_name()`/`reap_request()`
correctly build the supported `DELETE ?sandbox_id=` form with no injection
surface given docker's own name-character constraints, the old-prefix
regression is pinned, 15/15 tests pass fresh, `py_compile` is clean, and the
bundle's `--verify` liveness gate passes. The underlying "confirmed live"
fact (real container names, 200 vs 422) was supplied by the ticket at filing
time, not fabricated by the BUILD lane inside its own sandbox, and this
verify lane hit the identical docker-access wall trying to re-derive it
independently — that gap is a pre-existing fleet access-wall issue (tracked
under wq#4152), not a defect in this diff.

No escalation needed (no contradiction).
