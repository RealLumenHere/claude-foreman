#!/usr/bin/env python3
"""admission_gate.py — shared admission check for anything about to spawn a worker
(wq#2631, wq#2647). Combines the cloud-concurrency ceiling (harness/concurrency.py,
wq#616) with CPU load/PSI backpressure (cpu_pressure.py, mirrors the wq#844 memory-PSI
gate) into one CLI a caller can shell out to before spawning — the dispatcher's own
_do_pass calls the same two checks in-process (see the CPU-deny block near the wq#844
psi_pressure block), and out-of-process callers like /home/lumen/bin/dispatch-batch (a
separate bin/ repo a foreman worker cannot edit — ISSUE-LOG #743) get the identical
verdict via this subprocess-safe entry point instead of duplicating the logic.

Usage:
  admission_gate.py check [--prefix P] [--ceiling N] [--exclude-local]
    exit 0  -> admit (prints {"verdict": "ok", "reason": ...})
    exit 1  -> deny  (prints {"verdict": "deny", "reason": ...})

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

import cpu_pressure  # noqa: E402


def _override():
    if os.environ.get("ADMISSION_GATE_OVERRIDE") == "1":
        reason = os.environ.get("ADMISSION_GATE_OVERRIDE_REASON", "").strip()
        if reason:
            return reason
    return None


def check(prefix="fworker-wq", ceiling=concurrency.CLOUD_CEILING, exclude_local=False,
          cpu_cfg=None):
    """Returns (verdict, reason, detail) where verdict is "ok" or "deny".

    detail carries the raw ceiling/CPU numbers for logging/debugging even on an "ok"
    verdict; reason is the short human string a caller should log next to QUEUED/spawn.
    """
    override_reason = _override()
    if override_reason is not None:
        return "ok", f"admission_gate_override: {override_reason}", {}

    avail, units = concurrency.slots_available(prefix, ceiling, exclude_local=exclude_local)
    detail = {"available_slots": avail, "running": len(units), "ceiling": ceiling}
    if avail <= 0:
        return "deny", f"concurrency_ceiling: {len(units)}/{ceiling} slots in use", detail

    cpu_verdict, cpu_reason = cpu_pressure.check(cpu_cfg)
    detail["cpu_verdict"] = cpu_verdict
    detail["cpu_reason"] = cpu_reason
    if cpu_verdict == "deny":
        return "deny", f"cpu_pressure: {cpu_reason}", detail

    return "ok", f"slots={avail}/{ceiling} cpu={cpu_reason}", detail


def cmd_check(args):
    verdict, reason, detail = check(args.prefix, args.ceiling, args.exclude_local)
    print(json.dumps({"verdict": verdict, "reason": reason, **detail}))
    return 0 if verdict == "ok" else 1


def main():
    ap = argparse.ArgumentParser(description="admission_gate — concurrency + CPU admission check")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_chk = sub.add_parser("check", help="exit 0 to admit, 1 to deny")
    p_chk.add_argument("--prefix", default="fworker-wq")
    p_chk.add_argument("--ceiling", type=int, default=concurrency.CLOUD_CEILING)
    p_chk.add_argument("--exclude-local", action="store_true")
    p_chk.set_defaults(func=cmd_check)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
