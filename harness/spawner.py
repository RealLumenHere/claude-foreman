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

wq#1893: that loser path had a gap — a worker that launched and then died WITHOUT
writing attempt-N.json (operator SIGTERM, OOM, node crash) left its reservation row
'reserved' with nobody left to release it, locking the ticket's attempt number for the
full SPAWN_RESERVE_TTL_S (wq#1883: five passes logged spawn_failed over 02:55-03:18Z
2026-09-13 before the TTL finally cleared it). reserve_spawn() now checks
reservation_owner_alive() on a duplicate hit — is a `fworker-wq<ticket>` unit actually
running? — and reclaims (release + re-reserve) a dead owner's row in the SAME pass
instead of waiting out the TTL; a live owner still refuses, unchanged. And
settle_spawn(), called from harness/fworker.py's write_attempt_state on every attempt
write, clears the row as soon as the attempt actually lands instead of leaving every
settled spawn 'reserved' until the TTL. The TTL stays as the last-resort net for
whatever neither path catches.

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

# wq#1893: a reservation younger than this is never reclaimed, no matter what
# reservation_owner_alive() says — its owner has simply not Popen'd yet (dispatch_one's
# own `lumen-run start` call can take up to its 30s subprocess timeout, plus however
# long the unit takes to register as "running"), NOT died. 60s is comfortably above
# that window and comfortably below a dispatcher pass interval (wq#1883: 02:55-03:18Z
# 2026-09-13, 5 passes ~4-5min apart) — a genuinely dead owner still gets reclaimed on
# the very next pass, not after the full 6h TTL.
DEAD_OWNER_GRACE_S = float(os.environ.get("FOREMAN_SPAWN_DEAD_OWNER_GRACE_S", "60"))


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


def reservation_owner_alive(ticket_id):
    """wq#1893: is SOME fworker unit for this ticket currently running, per `lumen-run
    list` (concurrency.py's own list_running_units — the identical signal the
    concurrency cap and wq#1498's holder_unit_running both read)? A held reservation
    whose ticket has no running unit means the owner (a dispatcher pass that reserved
    but never Popen'd, or a worker that launched then died — SIGTERM/OOM/node crash —
    without reaching write_attempt_state) is dead.

    Fails CLOSED (assume alive) on any lumen-run trouble, matching wq#1498's
    holder_unit_running: a liveness check that can't reach lumen-run must never be
    read as 'confirmed dead'. Listed against the full fworker-wq* prefix and checked
    by exact name, not `startswith(f"fworker-wq{ticket_id}")` — wq#1893 vs wq#18930
    would otherwise prefix-collide."""
    try:
        units = concurrency.list_running_units(prefix="fworker-wq")
    except concurrency.LumenRunError:
        return True
    return f"fworker-wq{ticket_id}" in units


def reserve_spawn(ticket_id, attempt, owner):
    """Atomic reserve for (ticket_id, attempt) via despag's control_sqlite idem_reserve
    (wq#1807/E.5). True: we won, caller may spawn. False: someone else already holds
    this exact (ticket, attempt) reservation — the caller must exit before Popen.
    idem_reserve has no real await point (pure sqlite under one BEGIN IMMEDIATE), so
    asyncio.run() per call is just a sync wrapper, not a background task.

    wq#1893: a duplicate hit no longer means an automatic refusal — first check
    whether the reservation's owner is still alive. A dead owner's row is reclaimed
    (released, then re-reserved under this call's `owner`) in the SAME pass rather
    than left to log spawn_failed every pass until SPAWN_RESERVE_TTL_S expires
    (wq#1883). A live owner still refuses, unchanged — and so does an owner that is
    merely too YOUNG to judge yet (DEAD_OWNER_GRACE_S)."""
    store = ControlStore(SPAWN_RESERVE_DB)
    key = f"dispatch-spawn:{ticket_id}:{attempt}"
    existing = asyncio.run(store.idem_reserve(key, owner, ttl_s=SPAWN_RESERVE_TTL_S))
    if existing is None:
        return True
    age_s = time.time() - float(existing.get("ts") or 0)
    if age_s < DEAD_OWNER_GRACE_S or reservation_owner_alive(ticket_id):
        return False
    asyncio.run(store.idem_release(key, existing.get("resp_id")))
    existing = asyncio.run(store.idem_reserve(key, owner, ttl_s=SPAWN_RESERVE_TTL_S))
    if existing is not None:
        # someone else reclaimed it first in the same race window — defer to them.
        return False
    log(f"wq#{ticket_id} attempt {attempt}: reservation reclaimed from dead owner (wq#1893)")
    return True


def release_spawn(ticket_id, attempt, owner):
    """Best-effort release of a reservation whose spawn never actually launched (lumen-run
    failed before Popen), so the next dispatch pass can retry the same attempt number
    instead of being locked out until SPAWN_RESERVE_TTL_S expires."""
    store = ControlStore(SPAWN_RESERVE_DB)
    key = f"dispatch-spawn:{ticket_id}:{attempt}"
    asyncio.run(store.idem_release(key, owner))


def settle_spawn(ticket_id, attempt):
    """wq#1893: clear the (ticket, attempt) reservation once fworker has actually
    written attempt-N.json for it, so spawn_reserve.db reflects live spawns instead of
    every settled row sitting 'reserved' until SPAWN_RESERVE_TTL_S. Called from
    harness/fworker.py's write_attempt_state, which knows ticket_id/attempt but not
    this dispatch pass's `owner` string — so it settles WHATEVER resp_id currently
    holds the key rather than requiring an owner match. Safe: idem_release only
    deletes a reservation whose resp_id still matches what was just read, so a racing
    reclaim that reused the same key after this read can never be clobbered."""
    store = ControlStore(SPAWN_RESERVE_DB)
    key = f"dispatch-spawn:{ticket_id}:{attempt}"
    existing = asyncio.run(store.idem_get(key))
    if existing is None:
        return
    asyncio.run(store.idem_release(key, existing.get("resp_id")))


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


def dispatch_one(ticket_id, repo, env=None, model_override=None):
    """`env` (wq#2130): extra KEY=VALUE pairs for the worker process. lumen-run forwards only
    PATH into the unit, so router.dispatch_cmd puts them in front of the fworker command.
    `model_override` (wq#2154): the concrete model to run as (a routing group resolved to a
    member) — the governor check and the fworker --model both use it, never the ticket
    column's group name."""
    t = router.wq_show(ticket_id)
    model = model_override or t.get("model")
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
    kw = {k: v for k, v in (("env", env), ("model_override", model_override)) if v}
    cmd = router.dispatch_cmd(ticket_id, repo, **kw)
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
