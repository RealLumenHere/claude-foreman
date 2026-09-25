"""reroute_local_blocked.py — take the blocked-on-a-local-model backlog off the retired
local rung (wq#2583 AC part 2, finished under wq#2597).

wq#2576 measured the local rung at 0 SUCCESS in 166 fworker sessions and wq#2583 dropped
`local-worker` from the mechanical-edit and probe-build chains (research/models.yaml:330-331).
That fixed routing for NEW dispatches only. The tickets that were already blocked BECAUSE a
local session hung / crashed / false-completed kept `model=hathor-llama8b` (etc.) on the row,
so every one of them was still pinned to a model the fleet has since judged incapable — 62
rows at wq#2590 verify time, 54 of them blocked for a harness-side execution failure rather
than for anything about the work itself. wq#2583 closed `done` with that sweep unrun.

What this module does, per candidate row:

  * re-point `model` at the first CLOUD rung of the row's own archetype chain, not at a
    hardcoded model — the AC said "claude-sonnet-5 + the archetype-default effort", and for
    every archetype that was ever allowed to route local that IS the first cloud rung.
  * unblock ONLY when the block is a harness-side execution outcome (HANG, CRASH, KILLED,
    FALSE_COMPLETE, SPAWN_LOOP, turn/evidence ceiling). A block that says something about the
    WORK ("BLOCKED on a missing spec: ...") is re-pointed but left blocked: re-routing cannot
    make a missing spec appear, and mass-unblocking on a regex is how a sweep turns 1 stuck
    ticket into 1 stuck ticket that also burns a slot.
  * drop the stale claim left behind by the local session that died holding it, so the row is
    actually claimable afterwards (wq_close.py's release-then-claim ordering, same reason).

Every write goes through the `wq` CLI. Nothing here opens queue.db for writing: the row shape
(fence, lease, attempt) belongs to wq, and a sweep that edits it behind wq's back is a second
writer to a schema it does not own.

Dry-run is the default and prints the same per-row plan it would execute.

CLI:
    python3 harness/reroute_local_blocked.py                # plan only
    python3 harness/reroute_local_blocked.py --apply        # do it
    python3 harness/reroute_local_blocked.py --json         # plan as JSON
Exit 0 when the plan (or the applied sweep) leaves the wq#2597 liveness count at 0.
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys

WQ_BIN = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")
QUEUE_DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")

# The rung wq#2583 retired. Any row still carrying one of these is mis-routed by construction.
LOCAL_MODELS = ("local-worker", "hathor-llama8b", "qwen36-local", "nephthys-qwen14b")

# Blocks this sweep must not touch, verbatim from wq#2583's AC and wq#2597's liveness query:
# the block is about the work, the operator, a capability or a path, so a model swap is not
# the thing standing in the way.
PROTECTED = (
    ("REAL_BLOCK", "prefix"),
    ("FRANK", "substring"),
    ("CAPABILITY", "prefix"),
    ("CITED_PATH", "prefix"),
)

# First CLOUD rung of each archetype chain research/models.yaml:330-351 — the routing table is
# the source of truth, this is the subset of it that a blocked local row can legally carry
# (LOCAL_ALLOWED_ARCHETYPES never included rca-investigation, novel-design, fire-response, ...).
ARCHETYPE_TARGET = {
    "mechanical-edit": ("claude-sonnet-5", "low"),
    "probe-build": ("claude-sonnet-5", "medium"),
    "standard-engineering": ("claude-sonnet-5", "high"),
    "verify-pass": ("claude-sonnet-5", "high"),
}
# An unset archetype gets the model but NO effort: the router re-derives effort from the
# archetype it classifies at dispatch, and inventing one here would pin a guess over it.
UNSET_ARCHETYPE_TARGET = ("claude-sonnet-5", None)

# A local session died mid-flight. Nothing was learned about the ticket, so the block is an
# artifact of the rung and clearing it is the whole point of the sweep.
EXECUTION_BLOCK = re.compile(
    r"^(?:UNKNOWN|WORK_BLOCKED|SPAWN_LOOP|LIMIT|HANG|CRASH|KILLED|FALSE_COMPLETE)\b"
    r"|^evidence gate:\s*\d+\s+attempts",
    re.IGNORECASE)


def is_protected(block_reason):
    """True when wq#2583's AC excluded this block from the sweep."""
    reason = (block_reason or "").strip()
    for token, kind in PROTECTED:
        if kind == "prefix" and reason.upper().startswith(token):
            return True
        if kind == "substring" and token in reason.upper():
            return True
    return False


def target_for(archetype):
    """(model, effort) for a row's archetype. effort None means 'leave it to the router'.

    Raises on an archetype with no cloud rung mapped, rather than defaulting: a silent default
    is how a judgment-archetype ticket would end up dispatched at sonnet@low.
    """
    key = (archetype or "").strip()
    if not key:
        return UNSET_ARCHETYPE_TARGET
    if key not in ARCHETYPE_TARGET:
        raise KeyError(f"no cloud rung mapped for archetype {key!r} "
                       f"(research/models.yaml routing: add it or route the row by hand)")
    return ARCHETYPE_TARGET[key]


def classify(row):
    """The plan for one row: what to set, and whether the block survives the sweep.

    `row` is a dict with id/archetype/block_reason/claimed_by.
    """
    model, effort = target_for(row.get("archetype"))
    execution = bool(EXECUTION_BLOCK.search((row.get("block_reason") or "").strip()))
    return {
        "id": int(row["id"]),
        "from_model": row.get("model"),
        "model": model,
        "effort": effort,
        "unblock": execution,
        # A row whose local session died still holds the claim it died with; `wq claim --id`
        # would refuse the next dispatch on the stale holder.
        "release": bool((row.get("claimed_by") or "").strip()) and execution,
        "keep_blocked_because": None if execution else "block is about the work, not the rung",
        "block_reason": (row.get("block_reason") or "").strip(),
    }


def candidates(db=None):
    """Blocked rows still pinned to the retired local rung, minus the protected blocks."""
    con = sqlite3.connect(f"file:{db or QUEUE_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        marks = ",".join("?" * len(LOCAL_MODELS))
        rows = con.execute(
            f"SELECT id, model, effort, archetype, block_reason, claimed_by, pri, title "
            f"FROM tasks WHERE status='blocked' AND model IN ({marks}) ORDER BY id",
            LOCAL_MODELS).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows if not is_protected(r["block_reason"])]


def liveness_count(db=None):
    """wq#2597's own liveness query: 0 once the sweep has run."""
    return len(candidates(db=db))


def _wq(args, agent):
    env = dict(os.environ, WQ_AGENT=agent)
    return subprocess.run([WQ_BIN] + [str(a) for a in args],
                          capture_output=True, text=True, env=env)


def apply_plan(plan, agent, log=print):
    """Execute one row's plan. Returns (ok, [step strings]).

    Ordering is load-bearing: re-point the model BEFORE unblocking, so the row is never open
    and mis-routed at the same time — the dispatcher polls continuously and would happily
    claim it onto the rung we are removing.
    """
    steps, ok = [], True
    edit = ["edit", plan["id"], "--model", plan["model"]]
    if plan["effort"]:
        edit += ["--effort", plan["effort"]]
    p = _wq(edit, agent)
    steps.append(f"model={plan['model']} effort={plan['effort'] or '(router)'} rc={p.returncode}")
    if p.returncode != 0:
        return False, steps + [f"REFUSED: {(p.stderr or p.stdout).strip()[:200]}"]

    if plan["unblock"]:
        p = _wq(["edit", plan["id"], "--status", "open"], agent)
        steps.append(f"unblock rc={p.returncode}")
        ok = ok and p.returncode == 0
        if plan["release"] and p.returncode == 0:
            r = _wq(["release", plan["id"]], agent)
            steps.append(f"release-stale-claim rc={r.returncode}")
    else:
        steps.append(f"left blocked: {plan['keep_blocked_because']}")
    for s in steps:
        log(f"  wq#{plan['id']}: {s}")
    return ok, steps


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Re-route blocked tickets off the retired local rung (wq#2583 AC2).",
        epilog="Dry-run by default. The last line is LIVENESS=<n>; 0 satisfies wq#2597.")
    ap.add_argument("--apply", action="store_true", help="write the plan via the wq CLI")
    ap.add_argument("--db", default=None, help=f"queue.db (default {QUEUE_DB})")
    ap.add_argument("--agent", default="reroute-wq2597", help="WQ_AGENT for the writes")
    ap.add_argument("--json", action="store_true", help="plan as JSON on stdout")
    a = ap.parse_args(argv)

    rows = candidates(db=a.db)
    plans, unmapped = [], []
    for row in rows:
        try:
            plans.append(classify(row))
        except KeyError as e:
            unmapped.append({"id": row["id"], "error": str(e)})

    if a.json:
        print(json.dumps({"plans": plans, "unmapped": unmapped,
                          "liveness_before": len(rows)}, indent=2))
    else:
        for p in plans:
            verb = "reroute+unblock" if p["unblock"] else "reroute, stay blocked"
            print(f"wq#{p['id']:>5} {p['from_model']:>16} -> {p['model']}"
                  f"@{p['effort'] or 'router'}  {verb}  [{p['block_reason'][:44]}]")
        for u in unmapped:
            print(f"wq#{u['id']:>5} SKIPPED: {u['error']}")
        print(f"candidates={len(rows)} rerouted={len(plans)} "
              f"unblocked={sum(1 for p in plans if p['unblock'])} skipped={len(unmapped)}")

    if a.apply:
        failed = [p["id"] for p in plans if not apply_plan(p, a.agent)[0]]
        if failed:
            print(f"REFUSED on {len(failed)} row(s): {failed}", file=sys.stderr)

    remaining = liveness_count(db=a.db) if a.apply else len(rows)
    print(f"LIVENESS={remaining}")
    return 0 if remaining == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
