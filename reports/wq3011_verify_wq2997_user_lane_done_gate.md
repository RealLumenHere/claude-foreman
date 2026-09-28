# wq#3011 — verify-pass of wq#2997 (PR #1297): CONFIRMED

Archetype 8 adversarial verify of `wq done` disagreeing with `wq claim` on the
'user'-lane rule. Author claude-sonnet-5; verifier claude-sonnet-5@high, fresh
instance (no escalation — no contradiction found). Diff: `ops/wq2997-user-lane-done-gate/ensure_wq_user_lane_done_gate.py`
(+247, new), `harness/tests/test_wq2997_user_lane_done_gate.py` (+148, new).
Already merged to `main` (commits `0072df0b`/`c1bce0d2`) and applied live to
`/home/lumen/bin/wq`.

## What the PR claims

`cmd_claim` refuses the `'user'` lane unconditionally (`wq:1559`, `wq:1593` for
`--id`, `wq:3078`-ish for `hold`) — Frank-only, never agent-claimable.
`cmd_done` had no matching check: `owner_ok()` short-circuits `True` whenever
`claimed_by` is falsy, and a `'user'`-lane ticket can never carry a live claim
(claim always refuses it first), so that ownership gate was never in a
position to catch a `done` on an unclaimed user-lane row. On 2026-09-28,
`wq claim --id 1304` correctly refused; `wq done 1304 --no-artifact ...`
still closed it clean. Fix: `cmd_done` now looks up the ticket's lane before
any other gate and refuses a `'user'`-lane ticket unless the caller passes
`--frank-directed "<reason>"`, which is required (not a bare match to claim's
refusal) because there is no claim record to point at for "who authorized
this" — the reason gets stamped onto notes as `[FRANK-DIRECTED: ...]`, same
convention as `--no-artifact`/`--unlanded`/`--reclose`. Shipped as a keeper
patch (`ensure_wq_user_lane_done_gate.py --apply`, same shape as
`ops/wq1498-dead-worker-release`) because `wq` is rewritten in place by
whichever lane touches it next.

## What I did to try to break it

1. **Read the code cold.** Confirmed `owner_ok()`'s `if not claimed_by or
   caller == claimed_by: return True` short-circuit (`/home/lumen/bin/wq:1033`)
   and `cmd_claim`'s unconditional `'user'`-lane refusal, both the bare form
   (`wq:1559-1560`) and the `--id` form (`wq:1593-1596`), and `hold`'s twin at
   `wq:3101`. All three match the PR's description exactly — this is a real
   root cause, not a fabricated one.

2. **Confirmed the original incident is real**: `wq show 1304` — `claimed_by:
   None`, `status: done`, closed 2026-09-28 12:32:57, no claim ever recorded.
   Matches the ticket's detail field verbatim.

3. **Ran the shipped test suite** (`cd harness && python3 -m pytest
   tests/test_wq2997_user_lane_done_gate.py -v`, needed because
   `harness/fworker.py` isn't importable from repo root) — 6/6 pass, including
   the keeper alarm (`test_the_live_wq_is_still_patched`).

4. **Confirmed the live binary actually carries the patch**, not just the
   repo: `python3 ops/wq2997-user-lane-done-gate/ensure_wq_user_lane_done_gate.py
   --check --json` against `/home/lumen/bin/wq` → all three anchors
   `"applied"`.

5. **Adversarial probe beyond the shipped tests** (own scratch-DB script, not
   re-running the author's probe verbatim): added a `'user'`-lane ticket,
   closed it with `--frank-directed` (succeeds), then tried
   `wq done <id> --reclose "editing notes only"` **without**
   `--frank-directed` on the now-`done` ticket — still refused (exit 10, same
   wq#2997 message). The lane gate runs unconditionally before any other
   branch in `cmd_done`, so `--reclose` cannot walk around it. Also swept
   `wq --help` for any subcommand that mutates `tasks.lane` after `add` —
   none exists (lane is set once at creation and never touched again by any
   other verb), so there's no "flip the lane, close, flip it back" escape
   hatch.

6. **Checked for exit-code collisions**: the new refusal uses `sys.exit(10)`,
   unique across `wq` (every other `done` gate uses 1/2/3/5/6/7/8/9).
   `harness/fworker.py` only branches on `returncode != 0` generically
   (`fworker.py:771-772`), so this is a cosmetic inconsistency, not a
   functional bug — noted, not blocking.

7. **Checked `--frank-directed` isn't itself a security boundary**: it's a
   self-asserted string, same trust model as `--no-artifact`/`--unlanded`. Not
   a gap introduced by this PR — matches the existing pattern for every other
   `done` override flag.

## The flagged gap: no `--verify` handler (ops/VERIFY_CONTRACT.md, wq#1696)

The dispatched ticket explicitly named this bundle as missing a `--verify`
handler. Confirmed: `ops/wq2997-user-lane-done-gate/` shipped with only
`ensure_wq_user_lane_done_gate.py`, no `install.sh` at all. Per
`harness/liveness.py:432-440`, `ops_verified(bundle)` returns `UNKNOWN` for
any bundle with no `install.sh` — so this bundle could never satisfy a
`Liveness:` line, and `ops-deploy-drift` (scoped to bundles carrying
`install.sh`, per `ops/deploy_triage.py:4`) would never even see it to alert
on drift. A repo-only check (the keeper script's own `--check`) does not
count per the ticket instructions and wq#1696.

**Fixed as part of this verify pass** (not a re-implementation of the
author's logic — pure verify tooling, per `ops/VERIFY_CONTRACT.md`'s
template): added `ops/wq2997-user-lane-done-gate/install.sh` with a
`--verify` handler that delegates to the existing, already-read-only
`ensure_wq_user_lane_done_gate.py --check` (no new checking logic
duplicated) and exits 0/1/2 per the contract, emitting one `MISS <patch>:
<state>` line per unapplied anchor.

**Proved the probe can fail** (wq#2630 — an acceptance probe must be able to
fail): reverted the three patches on a scratch copy of the live `wq`, pointed
`install.sh --verify` at the scratch file via `WQ_BIN`, and got `MISS` x3 +
`VERIFY FAIL (3)` + exit 1. Against the real live `/home/lumen/bin/wq`, exit
0 + `VERIFY OK`. Also ran it through the documented `bash`-not-on-allowlist
workaround (same shape as wq#3000's report):

```
lumen-run start --name wq3011-verify -- bash \
  /home/lumen/worktrees/wq3011-a1/ops/wq2997-user-lane-done-gate/install.sh --verify
lumen-run wait lumen-job-wq3011-verify-1790603566.service   # DONE:0
lumen-run log  lumen-job-wq3011-verify-1790603566.service   # VERIFY OK
```

**Wider observation, not fixed here (out of scope for this ticket):** every
sibling "keeper" bundle that patches `wq` in place the same way —
`wq1498-dead-worker-release`, `wq1479-close-verdict`,
`wq1539-release-dead-worker-order`, `wq1675-subsystem-ro`,
`wq1998-advisory-block-gate` — has the identical gap: `ensure_*.py` only, no
`install.sh`, so `ops_verified()` has silently never been able to see any of
them either. Worth its own ticket (a class fix, not a per-bundle one) rather
than folding into this verify pass.

## Verdict: CONFIRMED, no contradiction

The fix does exactly what it claims: a `'user'`-lane ticket now refuses
`done` the same way `claim`/`hold` already refuse it, unless
`--frank-directed "<reason>"` is on the record, and the reason is stamped
onto the ticket. Ordinary lanes are unaffected (test + my own scratch probe).
No bypass found (`--reclose` doesn't route around the gate; lane is
immutable after `add`). Live binary carries the patch. The bundle's missing
`--verify` handler (the one concrete gap the dispatch flagged) is now added,
delegates to the existing read-only checker, and was proven falsifiable in
both directions before being used to confirm liveness.

## Evidence commands (reproducible)

```
git -C /home/lumen/worktrees/wq3011-a1 show 0072df0b
git -C /home/lumen/worktrees/wq3011-a1 show c1bce0d2 --stat
/home/lumen/bin/wq show 1304
cd /home/lumen/worktrees/wq3011-a1/harness && python3 -m pytest tests/test_wq2997_user_lane_done_gate.py -v
python3 /home/lumen/worktrees/wq3011-a1/ops/wq2997-user-lane-done-gate/ensure_wq_user_lane_done_gate.py --check --json
lumen-run start --name wq3011-verify -- bash /home/lumen/worktrees/wq3011-a1/ops/wq2997-user-lane-done-gate/install.sh --verify
lumen-run wait <unit>; lumen-run log <unit>
```
