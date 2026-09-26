#!/usr/bin/env python3
"""admission_gate.py — shared admission check for anything about to spawn a worker
(wq#2631, wq#2647, wq#2790). Combines per-partition concurrency (harness/concurrency.py,
wq#616) with CPU load/PSI backpressure (cpu_pressure.py, mirrors the wq#844 memory-PSI
gate) into one CLI a caller can shell out to before spawning — the dispatcher's own
_do_pass calls the same checks in-process (_admit_local/_admit_cloud in
foreman_dispatcher.py, and the CPU-deny block near the wq#844 psi_pressure block), and
out-of-process callers like /home/lumen/bin/dispatch-batch (a separate bin/ repo a
foreman worker cannot edit — ISSUE-LOG #743) get the identical verdict via this
subprocess-safe entry point instead of duplicating the logic.

Usage:
  admission_gate.py check [--prefix P] [--ticket N] [--model M]
                           [--ceiling N] [--exclude-local]
    exit 0  -> admit (prints {"verdict": "ok", "reason": ...})
    exit 1  -> deny  (prints {"verdict": "deny", "reason": ...})

wq#2127/wq#2790: with --ticket (its model resolved via concurrency.ticket_model) or
--model given directly, the check is PARTITIONED, matching foreman_dispatcher's own
_gate_candidate split — a local-pool model (concurrency.is_local_model) is checked
against dispatcher/local_capacity.py's per-backend free slots plus the local_ceiling
clamp, exactly as _admit_local does (group models resolve to a member, wq#2154); a
cloud model is checked against the SAME budget-aware cloud_ceiling the dispatcher pass
enforces (burn_rate.budget_ceiling, wq#2426), counting cloud units only
(exclude_local=True) — never the flat harness/concurrency.CLOUD_CEILING=3 that used to
apply to every fworker-wq* unit regardless of pool. cheap_cloud (deepseek-v4-flash,
wq#2081) is a local-pool model (concurrency.is_local_model), so it takes the local path
above and never touches the Anthropic budget ceiling; it has no local_backends row, so
it is bounded by the local_ceiling clamp alone, the same as the dispatcher's real
_admit_local for an unconfigured local model.

With neither --ticket nor --model (dispatch-batch's call today), the check is
UNPARTITIONED exactly as before, except the default ceiling is read from config.json's
cloud_ceiling instead of a hardcoded 3 — an explicit --ceiling still overrides it.

Emergency override: setting BOTH ADMISSION_GATE_OVERRIDE=1 and
ADMISSION_GATE_OVERRIDE_REASON=<non-empty text> forces an "ok" verdict (still printed,
with the override reason included) regardless of ceiling/CPU state. Both are required —
a bare ADMISSION_GATE_OVERRIDE=1 with no reason does NOT override, so a stray env var
can't silently punch through the gate.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "harness"))
import concurrency  # noqa: E402

import burn_rate  # noqa: E402
import cpu_pressure  # noqa: E402
import local_capacity  # noqa: E402

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("FOREMAN_DISPATCHER_CONFIG",
                             os.path.join(DISPATCHER_DIR, "config.json"))
GOVERNOR_STATE = os.environ.get("FOREMAN_STATE_JSON",
                                os.path.join(DISPATCHER_DIR, "..", "state.json"))


def _override():
    if os.environ.get("ADMISSION_GATE_OVERRIDE") == "1":
        reason = os.environ.get("ADMISSION_GATE_OVERRIDE_REASON", "").strip()
        if reason:
            return reason
    return None


def _load_config(path=None):
    """Plain working-tree read of config.json — admission_gate is a read-only
    subprocess check, not the dispatcher's own fail-closed policy load
    (foreman_dispatcher.load_config's git-HEAD read), so an unreadable or malformed
    file must not crash a caller like dispatch-batch; it just loses the tapered
    ceiling and falls back to the module defaults below."""
    try:
        with open(path or CONFIG_PATH) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _governor_limits(path=None):
    """Same read as foreman_dispatcher.governor_limits(), duplicated here rather than
    imported so admission_gate never pulls in the dispatcher's full gate/spawn stack
    for one JSON read."""
    try:
        with open(path or GOVERNOR_STATE) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {}
    limits = st.get("limits") if isinstance(st, dict) else None
    return limits if isinstance(limits, dict) else {}


def cloud_ceiling_for(model, cfg):
    """The budget-aware cloud ceiling for `model` — wq#2426's config cloud_ceiling
    tapered by spendable budget, the same computation
    foreman_dispatcher._PassCtx.cloud_ceiling() memoizes per pass. burn_rate.budget_ceiling
    already takes plain values (limits, ceiling, cfg), not a DispatchContext, so this is
    that same call factored out for a caller with no pass/context at all. Returns
    (ceiling, detail)."""
    ceiling = cfg.get("cloud_ceiling", concurrency.CLOUD_CEILING)
    limits = _governor_limits()
    burn_cfg = burn_rate.merge_config(cfg.get("burn_rate"))
    pools = burn_rate.pools_for_model(model)
    return burn_rate.budget_ceiling(limits, ceiling, burn_cfg, pools=pools)


def _unit_models(units):
    """Model of each unit in `units` (wq#2154: the unit's own --model wins over the
    ticket column, which keeps a group name the dispatcher resolved to a member)."""
    models = []
    for u in units:
        m = concurrency.TICKET_UNIT_RE.match(u)
        models.append(concurrency.unit_model(u) or
                      (concurrency.ticket_model(m.group("id")) if m else None))
    return models


def _local_verdict(model, cfg, prefix="fworker-wq"):
    """Local-pool admission for `model` — the local_ceiling clamp _admit_local checks
    first, then (for a Mac-pool model with a local_backends row) the same per-backend
    free-slots check, group resolution included (wq#2154). A cheap_cloud model
    (concurrency.is_mac_local_model False, wq#2081) has no local_backends row and never
    draws on the Anthropic budget ceiling either — the clamp above is its only bound,
    exactly as an unconfigured local model in _admit_local."""
    local_ceiling = cfg.get("local_ceiling", concurrency.LOCAL_CEILING)
    units = concurrency.list_running_units(prefix)
    local_units = [u for u in units if concurrency.is_local_unit(u)]
    n_local = len(local_units)
    detail = {"partition": "local", "model": model, "ceiling": local_ceiling, "running": n_local}
    if n_local >= local_ceiling:
        return "deny", f"local_ceiling: {n_local}/{local_ceiling} local unit(s) running", detail

    if not concurrency.is_mac_local_model(model):
        return "ok", f"local:{model} {n_local}/{local_ceiling} local unit(s)", detail

    cap = local_capacity.LocalCapacity(cfg.get("local_backends"),
                                       lambda: _unit_models(local_units))
    if cap.is_group(model):
        detail["free_slots"] = cap.free_slots(model)
        member = cap.pick_member(model)
        if member is None:
            return ("deny", f"local_slots_full: {model}: no healthy member with a "
                    "free slot", detail)
        detail["member"] = member
        return "ok", f"local:{model}->{member} free_slots={detail['free_slots']}", detail
    if cap.knows(model):
        free = cap.free_slots(model)
        detail["free_slots"] = free
        if free <= 0:
            return "deny", f"local_slots_full: {model}: 0 free slot(s)", detail
        return "ok", f"local:{model} free_slots={free}", detail
    return ("ok", f"local:{model} {n_local}/{local_ceiling} local unit(s) "
            "(unconfigured backend)", detail)


def _cloud_verdict(model, cfg, prefix="fworker-wq"):
    """Cloud-pool admission for `model`: the budget-aware ceiling, counting cloud units
    only (T4: local-rung units never eat a cloud slot)."""
    ceiling, ceiling_detail = cloud_ceiling_for(model, cfg)
    avail, units = concurrency.slots_available(prefix, ceiling, exclude_local=True)
    detail = {"partition": "cloud", "model": model, "ceiling": ceiling,
             "running": len(units), "ceiling_detail": ceiling_detail}
    if avail <= 0:
        return "deny", f"concurrency_ceiling: {len(units)}/{ceiling} cloud slots in use", detail
    return "ok", f"cloud:{model} slots={avail}/{ceiling}", detail


def _with_cpu(verdict, reason, detail, cpu_cfg):
    if verdict == "deny":
        return verdict, reason, detail
    cpu_verdict, cpu_reason = cpu_pressure.check(cpu_cfg)
    detail["cpu_verdict"] = cpu_verdict
    detail["cpu_reason"] = cpu_reason
    if cpu_verdict == "deny":
        return "deny", f"cpu_pressure: {cpu_reason}", detail
    return "ok", f"{reason} cpu={cpu_reason}", detail


def check(prefix="fworker-wq", ceiling=None, exclude_local=False, cpu_cfg=None,
          ticket=None, model=None, config_path=None):
    """Returns (verdict, reason, detail) where verdict is "ok" or "deny".

    detail carries the raw ceiling/CPU numbers for logging/debugging even on an "ok"
    verdict; reason is the short human string a caller should log next to QUEUED/spawn.

    With `ticket` or `model`, the check is partitioned (see module docstring). Without
    either, this is the pre-wq#2790 unpartitioned check: a flat ceiling (explicit
    `ceiling`, else config.json cloud_ceiling) against every fworker-wq* unit.
    """
    override_reason = _override()
    if override_reason is not None:
        return "ok", f"admission_gate_override: {override_reason}", {}

    if model is None and ticket is not None:
        model = concurrency.ticket_model(ticket)

    if model is not None:
        cfg = _load_config(config_path)
        if concurrency.is_local_model(model):
            verdict, reason, detail = _local_verdict(model, cfg, prefix)
        else:
            verdict, reason, detail = _cloud_verdict(model, cfg, prefix)
        return _with_cpu(verdict, reason, detail, cpu_cfg)

    if ceiling is None:
        ceiling = _load_config(config_path).get("cloud_ceiling", concurrency.CLOUD_CEILING)
    avail, units = concurrency.slots_available(prefix, ceiling, exclude_local=exclude_local)
    detail = {"available_slots": avail, "running": len(units), "ceiling": ceiling}
    if avail <= 0:
        return "deny", f"concurrency_ceiling: {len(units)}/{ceiling} slots in use", detail
    return _with_cpu("ok", f"slots={avail}/{ceiling}", detail, cpu_cfg)


def cmd_check(args):
    verdict, reason, detail = check(args.prefix, args.ceiling, args.exclude_local,
                                    ticket=args.ticket, model=args.model)
    print(json.dumps({"verdict": verdict, "reason": reason, **detail}))
    return 0 if verdict == "ok" else 1


def main():
    ap = argparse.ArgumentParser(description="admission_gate — concurrency + CPU admission check")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_chk = sub.add_parser("check", help="exit 0 to admit, 1 to deny")
    p_chk.add_argument("--prefix", default="fworker-wq")
    p_chk.add_argument("--ticket", type=int, default=None,
                       help="resolve this ticket's model (wq show) and partition on it")
    p_chk.add_argument("--model", default=None,
                       help="partition on this model directly, skipping the wq lookup")
    p_chk.add_argument("--ceiling", type=int, default=None,
                       help="unpartitioned mode only; default is config.json cloud_ceiling")
    p_chk.add_argument("--exclude-local", action="store_true")
    p_chk.set_defaults(func=cmd_check)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
