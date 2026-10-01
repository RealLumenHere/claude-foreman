#!/usr/bin/env python3
"""fleet-selfheal sweep (wq#3354) — detect, fix, verify, close; no audit needed.

One timer-driven pass over the self-healing classes; class 3 (credential leaks) is
the separate inotify daemon leakwatch.py because it must be real-time.

  guards     CLASS 1  restart stopped guard timers; ticket what will not stay up
  crashloop  CLASS 2  >3 automatic restarts in 5 min -> ticket, page, stop + holddown
  disk       CLASS 4  a mount >=90% -> run the safe pruners, ticket if still over
  providers  CLASS 5  LiteLLM host 401/403 for 10 min -> cool its rungs, rotation ticket
  nodes      CLASS 6  fleet node off tailscale 30 min -> ticket with last-seen + power

A ticket is filed on the build lane, which is what the orchestrator dispatches fworkers
from, so "file a ticket" and "dispatch a worker" are the same act. Each class's
failure is caught and reported so one broken class never stops the others.

    selfheal_sweep.py [--only CLASS[,CLASS]] [--dry-run] [--config PATH] [--state PATH]

--dry-run touches nothing (no systemctl, no file writes, no tickets, no state save)
and prints what it would do. Exit 0 whatever it found (house guard convention wq#1657);
nonzero only if the sweep itself crashed.
"""
import argparse
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, "/home/lumen/agent")   # holddown_lib (wq#967)

import selfheal_common as common  # noqa: E402
import sh_crashloop  # noqa: E402
import sh_disk  # noqa: E402
import sh_guards  # noqa: E402
import sh_nodes  # noqa: E402
import sh_providers  # noqa: E402

CLASSES = {"guards": sh_guards, "crashloop": sh_crashloop, "disk": sh_disk,
           "providers": sh_providers, "nodes": sh_nodes}


def run(ctx, only=None):
    errors = []
    for name, mod in CLASSES.items():
        if only and name not in only:
            continue
        if not ctx.cfg.get(name, {}).get("enabled", True):
            continue
        try:
            mod.sweep(ctx)
        except Exception:  # noqa: BLE001 — isolate classes from each other
            errors.append(name)
            ctx.note(name, "CLASS CRASHED: " + traceback.format_exc(limit=3).replace("\n", " | "))
            if not ctx.dry_run:
                ctx.out.alert(f"selfheal-class-crash:{name}",
                              f"fleet-selfheal class {name} crashed",
                              traceback.format_exc(limit=5)[-600:])
    return errors


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default="", help="comma list of: " + ",".join(CLASSES))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", default=common.CONFIG_PATH)
    ap.add_argument("--state", default=common.STATE_PATH)
    a = ap.parse_args(argv)
    only = {c for c in a.only.split(",") if c}
    unknown = only - set(CLASSES)
    if unknown:
        ap.error(f"unknown class(es): {', '.join(sorted(unknown))}")
    ctx = common.Ctx(common.load_config(a.config), common.load_state(a.state),
                     dry_run=a.dry_run)
    errors = run(ctx, only)
    for line in ctx.actions:
        print(line)
    if not a.dry_run:
        common.save_state(ctx.state, a.state)
    return 1 if errors and len(errors) == len(only or CLASSES) else 0


if __name__ == "__main__":
    sys.exit(main())
