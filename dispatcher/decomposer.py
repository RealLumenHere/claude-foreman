#!/usr/bin/env python3
"""decomposer.py — split a DECOMPOSE-routed ticket into dispatchable sub-tickets (wq#1146).

The router sends long-horizon-campaign tickets to the DECOMPOSE rung ("never one worker").
Nothing consumed that rung, so those epics parked undispatched for good. This module is the
CORE of the consumer: given a parent ticket it asks a planner for sub-tickets, refuses any
plan that breaks a guardrail, and only then files them. It has no LLM, wq or dispatcher
knowledge of its own: the planner, the critic and the wq surface are injected, so every
guardrail is unit-testable and phase 2 (dispatcher wiring) and phase 3 (parent tracking) only
supply callables.

Guardrails, all fail-closed (a refusal files NOTHING):
  fan-out    MIN_SUBS..MAX_SUBS sub-tickets, unique titles, real bodies.
  recursion  a child must route to a non-DECOMPOSE archetype, and carries `Decompose-depth: n+1`;
             a parent already at MAX_DEPTH is refused, so decomposition cannot recurse.
  idempotent a parent that already has children is never re-decomposed (wq#1141 is the
             duplicate-worker failure this prevents). Children are found by the
             `Decomposed-from: wq#<parent>` marker line in their detail.
  critic     the plan passes a critic BEFORE any ticket is filed; a critic that raises is a
             refusal, not a pass.
  completion parent deps = children. wq and the dispatcher's Deps gate already hold a ticket
             back until its deps are done, so the parent returns to the DECOMPOSE branch only
             when every child is done; decompose() then sees "all children done" and closes it.

Children are filed in dependency order; a failure part-way returns `failed` with the ids that
were filed, leaves the parent UNLINKED and notes it, so a human sees a half-filed plan instead
of a silent one (the marker makes the next call report `already`, not file a second set).
"""
import re
from dataclasses import dataclass, field

MIN_SUBS = 2
MAX_SUBS = 8
MAX_DEPTH = 1                 # a child (depth 1) is never decomposed again
MIN_DETAIL_CHARS = 40
MAX_TITLE_CHARS = 120
MARK_FROM = "Decomposed-from: wq#"
MARK_DEPTH = "Decompose-depth: "
_DEPTH_RE = re.compile(r"^Decompose-depth:\s*(\d+)\s*$", re.M)


@dataclass(frozen=True)
class Sub:
    """One planned sub-ticket. `deps` are indices of EARLIER subs in the same plan."""
    title: str
    detail: str
    lane: str = ""            # "" -> the parent's lane
    archetype: str = ""
    deps: tuple = ()


@dataclass
class Result:
    status: str               # filed | closed | already | refused | failed
    children: list = field(default_factory=list)
    reasons: list = field(default_factory=list)


def parse_depth(detail):
    m = _DEPTH_RE.search(detail or "")
    return int(m.group(1)) if m else 0


def validate_plan(subs, *, parent_lane, valid_archetypes, routes_to_decompose, lanes=None):
    """List of problems with a plan; empty means acceptable."""
    errs = []
    if not MIN_SUBS <= len(subs) <= MAX_SUBS:
        errs.append(f"fan-out {len(subs)} outside {MIN_SUBS}..{MAX_SUBS}")
    seen = set()
    for i, s in enumerate(subs):
        tag = f"sub[{i}]"
        title = (s.title or "").strip()
        if not title or len(title) > MAX_TITLE_CHARS:
            errs.append(f"{tag} title empty or over {MAX_TITLE_CHARS} chars")
        if title.lower() in seen:
            errs.append(f"{tag} duplicate title {title!r}")
        seen.add(title.lower())
        if len((s.detail or "").strip()) < MIN_DETAIL_CHARS:
            errs.append(f"{tag} detail under {MIN_DETAIL_CHARS} chars, not a concrete task")
        if s.archetype not in valid_archetypes:
            errs.append(f"{tag} archetype {s.archetype!r} is not a routable archetype")
        elif routes_to_decompose(s.archetype):
            errs.append(f"{tag} archetype {s.archetype!r} routes to DECOMPOSE (recursion)")
        lane = s.lane or parent_lane
        if lanes is not None and lane not in lanes:
            errs.append(f"{tag} lane {lane!r} is not a dispatchable lane")
        for d in s.deps:
            if not isinstance(d, int) or not 0 <= d < i:
                errs.append(f"{tag} dep {d!r} must be an earlier sub index (0..{i - 1})")
    return errs


def decompose(parent, *, wq, plan_fn, critic_fn, valid_archetypes, routes_to_decompose,
              lanes=None):
    """Decompose `parent` (a wq-show dict). `wq` needs: children(parent_id) -> [{id, status}],
    add(title, detail, lane, archetype, deps) -> id, set_deps(parent_id, ids), note(id, text),
    done(id, notes). Returns a Result; every non-`filed` outcome except `failed` filed nothing."""
    pid = int(parent["id"])
    if parse_depth(parent.get("detail")) >= MAX_DEPTH:
        return Result("refused", reasons=[f"wq#{pid} is already a decomposed child "
                                          f"(depth >= {MAX_DEPTH}); not decomposing again"])

    existing = wq.children(pid)
    if existing:
        ids = [int(c["id"]) for c in existing]
        if all(str(c.get("status")) == "done" for c in existing):
            wq.done(pid, f"all {len(ids)} sub-tickets done: " + ", ".join(f"wq#{i}" for i in ids))
            return Result("closed", children=ids)
        wq.set_deps(pid, ids)     # idempotent re-link, never a second decomposition
        return Result("already", children=ids)

    try:
        subs = list(plan_fn(parent))
    except Exception as e:  # noqa: BLE001 — a planner failure files nothing
        return Result("refused", reasons=[f"planner failed: {type(e).__name__}: {str(e)[:120]}"])

    errs = validate_plan(subs, parent_lane=parent.get("lane", ""),
                         valid_archetypes=valid_archetypes,
                         routes_to_decompose=routes_to_decompose, lanes=lanes)
    if errs:
        return Result("refused", reasons=errs)

    try:
        ok, why = critic_fn(parent, subs)
    except Exception as e:  # noqa: BLE001 — fail closed
        return Result("refused", reasons=[f"critic failed: {type(e).__name__}: {str(e)[:120]}"])
    if not ok:
        return Result("refused", reasons=[f"critic rejected the plan: {str(why)[:300]}"])

    depth = parse_depth(parent.get("detail")) + 1
    ids = []
    for i, s in enumerate(subs):
        detail = (s.detail.rstrip() + f"\n\n{MARK_FROM}{pid}\n{MARK_DEPTH}{depth}\n")
        try:
            ids.append(int(wq.add(s.title.strip(), detail, s.lane or parent.get("lane", ""),
                                  s.archetype, [ids[d] for d in s.deps])))
        except Exception as e:  # noqa: BLE001
            wq.note(pid, f"DECOMPOSE FAILED after filing {len(ids)}/{len(subs)} sub-tickets "
                         f"({', '.join(f'wq#{n}' for n in ids) or 'none'}): "
                         f"{type(e).__name__}: {str(e)[:120]}. Parent NOT linked; human review.")
            return Result("failed", children=ids, reasons=[f"filing sub[{i}] failed: {e}"])
    wq.set_deps(pid, ids)
    wq.note(pid, "DECOMPOSED into " + ", ".join(f"wq#{n}" for n in ids)
            + ". Parent closes when all are done.")
    return Result("filed", children=ids)
