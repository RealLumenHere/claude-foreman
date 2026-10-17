# wq#4153 — verify-pass on wq#4149 ([openhands] 1.8 stack hardening + land readiness doc)

Author: claude-sonnet-5 · Verifier: claude-sonnet-5@high (fresh instance, wq#4153).
Landed commit under test: `2e0261bf` (main, merged via `2a016b76`/`8a5975f3`,
wq#4149 PR #2093).

## Claim under test

wq#4149's done notes claim it landed `research/OPENHANDS_1_8_READINESS.md` +
`ops/wq4149-openhands-1-8-hardening/` (`harden_app.py`, `reaper.py`,
`test_reaper.py`, 8/8 tests passing), added a real `/alive` liveness probe
(`check_openhands_alive()`) to `ops/wq4111-openhands-litellm-production/probe.py`,
updated `research/NETWORK_PREMORTEM.md` S1 with the `172.17.0.1:4000` binding,
and filed the items needing docker/host access as wq#4152 (lane `fleet`) since
this BUILD lane has no docker access. AC1 cited: `README.md:86-90`.

## What I checked

1. **Read the diff cold** — `git show 2e0261bf --stat`: 8 files, 521
   insertions/11 deletions across `probe.py`, `test_probe.py`,
   `ops/wq4149-openhands-1-8-hardening/{README,harden_app.py,reaper.py,
   test_reaper.py}`, `NETWORK_PREMORTEM.md`, `OPENHANDS_1_8_READINESS.md`.
2. **Ran the shipped tests, fresh.** `python3 test_reaper.py -v` — 8/8 OK.
   `python3 test_probe.py -v` — 8/8 OK. `py_compile` on all three `.py` files
   in the bundle — clean.
3. **Tried to break `reaper.reap_candidates` adversarially**, beyond the 8
   shipped cases: exact-boundary grace/idle thresholds (both reap, matching
   the documented `>=` semantics), a name that is a prefix-superset but
   missing the trailing dash (`openhands-runtimeX` vs `openhands-runtime-`,
   correctly NOT matched), an unhandled container status (`paused`, kept, no
   crash), empty container list, and containers missing `Name`/`State`
   entirely (no crash, kept). All 7 adversarial cases passed — the selection
   logic is sound against the inputs `docker inspect` can plausibly produce.
4. **Reproduced the live claims instead of trusting the doc.**
   - `python3 ops/wq4111-openhands-litellm-production/probe.py` live:
     `litellm_ready=true, openhands_up=true, openhands_alive=true,
     api_routing=spa_fallback` — matches the research doc's "re-verified live"
     section exactly, independently reproduced, not re-copied from the diff.
   - `ss -tln | grep -E ':3000|:4000'`: `172.17.0.1:3000`, `127.0.0.1:3000`,
     `172.17.0.1:4000`, `127.0.0.1:4000`, `100.117.66.102:4000`,
     `[fd7a:...]:4000` — matches `NETWORK_PREMORTEM.md` S1's edit exactly, and
     confirms no `0.0.0.0`/`[::]` on port 3000 (the "do not widen exposure"
     constraint holds).
   - `python3 harden_app.py --dry-run` — output byte-for-byte matches the
     README's "Verification run" transcript.
   - `wq show 4152` — ticket exists, lane `fleet`, `deps: 4149`, matches the
     filing claim. Its own status is `blocked` (ISSUE-LOG #1399: the "fleet"
     lane's `worker-settings.json` denies `docker *` the same way the BUILD
     lane's does, so the privileged work didn't actually get unblocked by
     redispatch) — this is a downstream consequence of a worker-settings gap,
     not a defect in wq#4149's diff, which only claimed to *file* the
     follow-up, not resolve it.
   - `test -e /home/lumen/.openhands` — exists, matches wq#4152's
     `Liveness: path_exists(...)` claim.
   - README.md:86-90 cited as AC1 — read back, matches the "Privileged-lane
     follow-up" section pointing at wq#4152 for items 2/3/4/6/8/9.
5. **Verify-contract gap (the ticket's explicit ask).** The ticket listed
   `wq4149-openhands-1-8-hardening` as a bundle with no `--verify` handler.
   Added `ops/wq4149-openhands-1-8-hardening/install.sh`: the bundle installs
   nothing (run-in-place tool, same posture as `wq1747-litellm-key-rotation`'s
   `install.sh`), so `--verify` checks the two things that would make it
   merged-but-not-live: (a) `harden_app.py`'s hardcoded `COMPOSE_DIR`/
   `WORKSPACE_DIR` targets still exist on this host, (b) the sibling
   `wq4111` bundle's `check_openhands_alive()` still exists and still answers
   a true liveness check against the running container (read-only GET, no
   docker, no sudo). Ran live via
   `lumen-run start -- bash .../install.sh --verify` → `VERIFY OK`, exit 0.
   Confirmed the probe can actually fail (wq#2630): ran a sibling copy with
   `COMPOSE_DIR` pointed at a nonexistent path the same way →
   `MISS absent: /home/lumen/containers/does-not-exist-wq4153 (...)`,
   `VERIFY FAIL (1)`, exit 1. Also confirmed the default (no-arg) path still
   runs `test_reaper.py` and prints the run-in-place usage, matching the
   `wq1747` pattern this bundle is modeled on.
6. **Checked the explicitly-deferred items stay honestly deferred.**
   `SANDBOX_NAME_PREFIX` in `reaper.py` is marked UNCONFIRMED in both the
   module docstring and the README table — it is not wired to a live
   `docker rm -f` anywhere in the diff, so there is no unconfirmed-guess path
   that could fire against the real fleet. `max_num_sandboxes`,
   docker-socket-proxy, and non-root app container are all left as
   recommendations in the README's "What's NOT here, and why" section, each
   with a stated reason it couldn't be confirmed from this lane (no
   `BRAVE_API_KEY`/search outage, no docker) rather than a guessed value —
   consistent with instinct 8.

## Verdict

**CONFIRM.** Every live, checkable claim in wq#4149's diff reproduced
independently and exactly: the 16 shipped unit tests (8 reaper + 8 probe)
pass, 7 additional adversarial cases against `reap_candidates` found no
crash or misclassification, the live `/alive` probe and port-binding claims
match the research doc's "re-verified live" section byte-for-byte against
a fresh run (not copied from the diff), the follow-up ticket (wq#4152) and
AC1 citation both check out, and the deferred items are genuinely deferred
rather than guessed. The one gap the ticket flagged — no `--verify` handler
on `ops/wq4149-openhands-1-8-hardening` — is now closed with a handler that
demonstrably passes on the live host and demonstrably fails on a broken
target.

No escalation needed (no contradiction).
