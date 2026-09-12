#!/usr/bin/env python3
"""spawner — concurrency-capped, staggered batch dispatch. BUILD_PLAN.md §T2b (wq#616).

Fans a list of ready wq tickets out through `lumen-run start ... fworker.py run ...`
(router.dispatch_cmd already builds that per-ticket command line — this module doesn't
reimplement ticket routing, only decides WHEN and HOW MANY to launch at once):

  - governor gate (T1) re-checked immediately before each spawn — cheap, avoids
    spinning up a systemd unit that fworker's own gate would just DEFER anyway.
  - concurrency cap (concurrency.py): never exceed `--ceiling` live fworker-wq* units
    (FOREMAN_RESEARCH.md §8 default 3); `--target`/default batch size is the "start 2"
    half of the same doctrine — callers wanting the full ceiling pass a bigger batch.
  - stagger 2-3s between spawns (§8 point 4: defensive against shared .claude.json /
    history writes at worker startup) — randomized within the window so a batch of
    same-second dispatches doesn't itself become a synchronized burst.

This replaces the T2A design doc's "ONE cloud worker at a time (fworker refuses to
start if another fworker-wq* unit is RUNNING)" placeholder, which was never actually
implemented in fworker.py — see harness/README.md's "Build-time discovery vs the
design" section. Does not replace fworker's own per-ticket governor/dep/claim gates;
those still run inside fworker regardless of how it was launched.

PHASE0_WIRING_SPEC.md E.5 (wq#1815): the concurrency ceiling above bounds HOW MANY spawn
at once, not whether the SAME (ticket, attempt) can be spawned twice — two dispatcher
passes (or a dispatcher restart mid-pass) racing on the same ready ticket could each
reach dispatch_one() and both exec `lumen-run start` before either's attempt-N.json
exists to say otherwise. ISSUE-LOG #4 hit this class at the wq-claim layer (wq#1141,
wq#1097 x2) before PR#46's lease+fence; this is the same fix one layer down, in the
dispatcher's own spawn path, using despag's control_sqlite (wq#1807) idempotency
reserve: an atomic `BEGIN IMMEDIATE` INSERT keyed `dispatch-spawn:<ticket>:<attempt>`
where `attempt` is self_heal.attempt_count()+1 (the attempt THIS spawn would create).
The loser's reserve call returns the winner's record instead of None and exits before
Popen; a spawn that fails to launch releases its reservation so the next pass can retry
the same attempt number.

Usage:
  spawner.py dispatch --repo PATH --ticket ID [--ticket ID ...]
             [--ceiling N] [--stagger-min S] [--stagger-max S] [--slot-wait-max S]
"""
import argparse
import asyncio
import json
import os
import random
import subprocess
import sys
import time
import uuid

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
import concurrency  # noqa: E402

ORCH_DIR = os.path.normpath(os.path.join(HARNESS_DIR, "..", "orchestrator"))
sys.path.insert(0, ORCH_DIR)
import router  # noqa: E402

DISPATCHER_DIR = os.path.normpath(os.path.join(HARNESS_DIR, "..", "dispatcher"))
sys.path.insert(0, DISPATCHER_DIR)
import self_heal  # noqa: E402 — attempt_count(), the same signal spawn_loop_guard uses

REPO_ROOT = os.path.normpath(os.path.join(HARNESS_DIR, ".."))
sys.path.insert(0, REPO_ROOT)
from despag.backing.control_sqlite import ControlStore  # noqa: E402 — wq#1807

FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
GOVERNOR_CLI = os.environ.get("FOREMAN_GOVERNOR_CLI",
                               os.path.join(FOREMAN_ROOT, "governor", "foreman_usaged.py"))

STAGGER_MIN_S = 2.0
STAGGER_MAX_S = 3.0
SLOT_POLL_S = float(os.environ.get("FOREMAN_SLOT_POLL_S", "5"))
DEFAULT_SLOT_WAIT_MAX_S = 1800  # give up waiting for a slot after 30 min

# wq#1815 (E.5): gitignored runtime/ path (ISSUE-LOG #41 convention) — mutable daemon
# state, never tracked. TTL is a safety net for a reservation whose owner crashed
# before releasing/settling it, sized above any real worker session (router.py's
# FIRE_MAX_RUNTIME_S is 2h; this is 3x that).
SPAWN_RESERVE_DB = os.environ.get(
    "FOREMAN_SPAWN_RESERVE_DB",
    os.path.join(FOREMAN_ROOT, "dispatcher", "runtime", "spawn_reserve.db"))
SPAWN_RESERVE_TTL_S = int(os.environ.get("FOREMAN_SPAWN_RESERVE_TTL_S", str(6 * 3600)))


def log(msg):
    print(f"[spawner {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}", flush=True)


def governor_allows(model):
    try:
        p = subprocess.run([sys.executable, GOVERNOR_CLI, "can-dispatch", model],
                            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"governor unreachable ({e}) — treating as deny")
        return False
    return p.returncode == 0


def reserve_spawn(ticket_id, attempt, owner):
    """Atomic reserve for (ticket_id, attempt) via despag's control_sqlite idem_reserve
    (wq#1807/E.5). True: we won, caller may spawn. False: someone else already holds
    this exact (ticket, attempt) reservation — the caller must exit before Popen.
    idem_reserve has no real await point (pure sqlite under one BEGIN IMMEDIATE), so
    asyncio.run() per call is just a sync wrapper, not a background task."""
    store = ControlStore(SPAWN_RESERVE_DB)
    key = f"dispatch-spawn:{ticket_id}:{attempt}"
    existing = asyncio.run(store.idem_reserve(key, owner, ttl_s=SPAWN_RESERVE_TTL_S))
    return existing is None


def release_spawn(ticket_id, attempt, owner):
    """Best-effort release of a reservation whose spawn never actually launched (lumen-run
    failed before Popen), so the next dispatch pass can retry the same attempt number
    instead of being locked out until SPAWN_RESERVE_TTL_S expires."""
    store = ControlStore(SPAWN_RESERVE_DB)
    key = f"dispatch-spawn:{ticket_id}:{attempt}"
    asyncio.run(store.idem_release(key, owner))


def wait_for_slot(prefix, ceiling, max_wait_s):
    """Polls concurrency.slots_available until a slot is free or max_wait_s elapses.
    A LumenRunError (can't reach lumen-run at all) is treated the same as "no slot" —
    fail toward under- not over-subscribing the shared login. T4: exclude_local=True —
    a busy local rung must never eat into this cloud-login ceiling (bounded by Lumen
    CPU instead, harness/concurrency.py's LOCAL_POOL_MODELS)."""
    waited = 0.0
    while True:
        try:
            avail, units = concurrency.slots_available(prefix, ceiling, exclude_local=True)
        except concurrency.LumenRunError as e:
            avail, units = 0, [f"<lumen-run unreachable: {e}>"]
        if avail > 0:
            return True
        if waited >= max_wait_s:
            log(f"gave up waiting for a concurrency slot after {waited:.0f}s "
                f"(ceiling={ceiling}, running={units})")
            return False
        time.sleep(SLOT_POLL_S)
        waited += SLOT_POLL_S


def dispatch_one(ticket_id, repo):
    t = router.wq_show(ticket_id)
    model = t.get("model")
    if model in (None, "", "None"):
        d = router.assign(ticket_id)
        if not d.get("rung"):
            log(f"wq#{ticket_id}: router could not assign a rung ({d['reasons']}) — skipping")
            return None
        model = d["rung"]["model"]
    if not governor_allows(model):
        log(f"wq#{ticket_id}: governor denies {model} right now — skipping this pass")
        return None
    attempt = self_heal.attempt_count(ticket_id) + 1
    owner = f"{os.getpid()}:{uuid.uuid4()}"
    if not reserve_spawn(ticket_id, attempt, owner):
        log(f"wq#{ticket_id} attempt {attempt}: spawn reservation already held — "
            f"duplicate spawn, refusing before Popen (E.5)")
        return None
    cmd = router.dispatch_cmd(ticket_id, repo)
    log(f"spawning wq#{ticket_id}: {' '.join(cmd)}")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"wq#{ticket_id}: lumen-run start failed to launch: {e}")
        release_spawn(ticket_id, attempt, owner)
        return None
    if p.returncode != 0:
        log(f"wq#{ticket_id}: lumen-run start failed rc={p.returncode}: {p.stdout} {p.stderr}")
        release_spawn(ticket_id, attempt, owner)
        return None
    return p.stdout.strip()


def dispatch_batch(ticket_ids, repo, *, ceiling=concurrency.CLOUD_CEILING,
                    prefix="fworker-wq", stagger_min=STAGGER_MIN_S, stagger_max=STAGGER_MAX_S,
                    slot_wait_max_s=DEFAULT_SLOT_WAIT_MAX_S):
    """Launches each ticket in order, never exceeding `ceiling` concurrently-running
    fworker-wq* units, staggered stagger_min-stagger_max seconds apart. Returns
    {ticket_id: unit_or_None} — None means skipped this pass (governor deny / slot
    timeout / routing failure), not a hard error; the ticket stays open in wq for a
    later pass to pick up."""
    results = {}
    for i, ticket_id in enumerate(ticket_ids):
        if not wait_for_slot(prefix, ceiling, slot_wait_max_s):
            results[ticket_id] = None
            continue
        results[ticket_id] = dispatch_one(ticket_id, repo)
        if i < len(ticket_ids) - 1:
            time.sleep(random.uniform(stagger_min, stagger_max))
    return results


def cmd_dispatch(args):
    results = dispatch_batch(args.ticket, args.repo, ceiling=args.ceiling,
                              stagger_min=args.stagger_min, stagger_max=args.stagger_max,
                              slot_wait_max_s=args.slot_wait_max)
    print(json.dumps(results, indent=2))
    return 0 if all(v is not None for v in results.values()) else 1


def main():
    ap = argparse.ArgumentParser(description="spawner — concurrency-capped batch dispatch")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("dispatch", help="launch a batch of ready tickets, cap+stagger")
    p.add_argument("--ticket", type=int, action="append", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--ceiling", type=int, default=concurrency.CLOUD_CEILING)
    p.add_argument("--stagger-min", type=float, default=STAGGER_MIN_S)
    p.add_argument("--stagger-max", type=float, default=STAGGER_MAX_S)
    p.add_argument("--slot-wait-max", type=int, default=DEFAULT_SLOT_WAIT_MAX_S)
    p.set_defaults(func=cmd_dispatch)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
