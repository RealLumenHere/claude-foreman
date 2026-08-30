# wq#1116 — two orchestrators on one queue

## What happened

2026-08-30 01:22–01:29Z, two claude.ai orchestrator sessions rode the same Commander over the
same ticket batch. Session B spawned `fworker-wq1100` at 01:22:44Z, watched a1/a2 die, hand-edited
`sentinel_probes.py`, opened PR#165, closed wq#1100 — and also closed **wq#1096 at 01:27:18 while
session A was still verifying it**. A's later `wq done 1096` then replaced B's note with its own.

Both sessions resolved to `lumen:<pid>`. Neither had run `wq whoami --register`, so wq#502's
holder check, wq#530's staleness rules and wq#594's concurrency report each had nothing to key on.

- **What held:** the claim-holder refusal. No two workers ever landed on one ticket.
- **What did not:** `owner_ok` waved through *any* caller that could not prove an identity, and
  `done` replaces notes wholesale, so the second close silently destroyed the first one's evidence.

## The thing that made this hard to fix

The obvious fix — "refuse non-holder writes from unregistered callers" — bricks the fleet, and it
took measuring the live DB to see why.

`resolve_identity` falls back to `hostname:pid`, and that pid is the **wq process's**, not the
session's. So a caller on the fallback path can never match a claim it made itself. 253 claims in
the live DB have that shape. Worse, `fworker` claimed each ticket under its own `lumen:<pid>` and
then handed the worker a *different* `WQ_AGENT` (`fworker-wq<id>-a<n>`), so **caller and holder
disagreed on every single dispatch in the fleet**. The backcompat branch wasn't a legacy nicety;
it was load-bearing.

That is also why the first cut of this fix was wrong. It kept an exception for "caller and holder
are both `host:pid` on the same host", reasoning that fworker's claim-then-close was
indistinguishable from the collision. It was — but that exception permits *exactly* the incident
(both sessions were `lumen:<pid>`) while still refusing fworker's real shape (a *named* caller
against a `host:pid` holder). It protected nothing and broke the thing it was meant to spare.

So the fix had to run the other way: **make the fleet's own identities honest first**, then the
ownership rule needs no name-shape exception at all.

## What changed

### 1. `harness/fworker.py` — claim under the identity that will close (the load-bearing half)

New `wq_identity(ticket_id)` → `fworker-wq<id>`, stable across attempts, because what wq owns is
the *ticket*, not the attempt. `wq_claim` claims under it, `build_env` exports it as the worker's
`WQ_AGENT`, and `main` pins it into `os.environ` before the claim so fworker's own
heartbeat/done/release are the holder too. `worker_id` stays per-attempt for logs, events and
worktree naming — conflating the two was the bug.

### 2. `/home/lumen/bin/wq` — `owner_ok` refuses foreign writes to a live claim

An unregistered caller may no longer heartbeat/done/release/block another identity's claim, with
one carve-out and no name-shape special cases: `claim_guarded()` is false when the claim has **no
live lease** — wq#530 already rules those free, and `blocked` drops the lease outright, which is
the dispatcher self-heal path — or when it predates the grandfather cutoff below.

Read-only subcommands never reach `owner_ok`, so `list`/`show` are untouched, per the ticket.
`WQ_STRICT=1` refuses regardless. Registered callers (wq#502 case 3) are unchanged.

### 3. `/home/lumen/bin/wq` — `done` no longer overwrites a closed ticket's evidence

`wq done` on an already-`done` ticket refuses with **exit 4** and quotes the notes it would have
destroyed; `--reclose "<reason>"` does it on the record. This one is deliberately
**identity-independent** — erasing a closure's citation is the damage, whoever does it, and A was
arguably entitled to close wq#1096. `release`/`block` on a done ticket stay the wq#841 reopen path.

### 4. `/home/lumen/bin/wq orch` — one orchestrator per lane-set

`wq orch --acquire <lanes>` leases a lane-set to one **registered** orchestrator; `wq orch` prints
who is driving what and since when; `wq orch --release` drops it. Stored as slocks under
`orch:<lane>` rather than a new table, so lease expiry, wq#530's never-purge-a-live-holder rule and
wq#568's retirement sweep all apply unchanged — a new table would have had to re-earn each.

Registration is **required** to acquire, and that is the point rather than a side effect. Every
mechanism above keys on a registered identity; making the lease the thing an orchestrator wants is
what makes registering the thing it does first.

## Rollout

`OWNER_STRICT_FROM` (env `WQ_OWNER_STRICT_FROM`, default `2026-08-31 00:00:00`) grandfathers claims
made before the cutoff. Six claims were in flight under the old identity scheme when this shipped;
refusing them would have failed closes for work already finished. The reclose guard and the orch
lease are in force immediately — they have no such dependency.

**Delete `OWNER_STRICT_FROM` and its single use in `claim_guarded()` once no pre-cutoff claim is
still open.** It is one constant and one comparison, kept deliberately trivial to remove.

## What this does not close

Two *unregistered* same-host drivers can still step on each other's in-flight claims if neither
takes an orch lease. Nothing at the wq chokepoint can separate them — wq#594's docstring works
through why no per-instance signal exists there — and the lease only helps callers that take one.
Boot-time registration is what closes the rest, and it lives outside this repo:

- **wq#1127** — `/home/lumen/minerva/context/claude_context.sh --core` should print the registered
  identity or refuse.
- **wq#1128** — `/home/lumen/foreman/FOREMAN_DISPATCH.md` (gitignored here, lives in the primary
  tree) and `lumen-session-start` should instruct the orchestrator to
  `wq whoami --register orch-<slug>` and then `wq orch --acquire <lanes>`.

Both files are outside a build worker's sandbox, which is why this ticket could not do them.

- **wq#1129** — remove the `OWNER_STRICT_FROM` grandfather once no pre-cutoff claim is open.

## Verifying

```
python3 ops/wq1116-orch-collision-guard/test_wq1116_guards.py   # 34 checks, own temp DB
python3 -m pytest harness/tests/test_wq_identity.py -q          # fworker identity, zero quota
```

`test_wq1116_guards.py` drives the real `/home/lumen/bin/wq` as a subprocess against a tempfile DB
— the thing under test is the refusal a second orchestrator actually sees, exit code included.
Section D replays the incident directly: B is refused A's live claim, B's note survives A's later
close, and the second orchestrator is refused the lane-set before it can dispatch.

`wq1116_owner_guard.diff` archives the `/home/lumen/bin/wq` change, which lands in the `bin` repo
rather than this one (same split as `ops/wq841-rework-durability/`).
