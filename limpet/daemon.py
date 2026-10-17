"""LIMPET registry daemon loop. The registry's reconcile() is the whole loop
body (scan drop-ins -> health -> resolve -> write unified.json); this wraps it
in a periodic timer so a dropped-in plugin is picked up without a restart
(lifecycle step 2). Meant to run detached (systemd unit / lumen-run), not
foreground-unattended per fleet instinct 5.

Run: python3 -m daemon --interval 30   (from the limpet/ dir, or with limpet/
on PYTHONPATH). Ref: design/wq3810_limpet_plugin_system.md.
"""
from __future__ import annotations

import argparse
import time

from plugin_registry import Registry


def run(interval_seconds=30, iterations=None, reg=None, sleep=time.sleep):
    """Reconcile every `interval_seconds`. `iterations` bounds the loop for
    tests (None = forever); `sleep` is injectable so tests don't actually wait."""
    reg = reg or Registry()
    count = 0
    while iterations is None or count < iterations:
        reg.reconcile(run_health=True)
        count += 1
        if iterations is not None and count >= iterations:
            break
        sleep(interval_seconds)
    return count


def main(argv=None):
    p = argparse.ArgumentParser(prog="limpet-registryd", description="LIMPET registry daemon")
    p.add_argument("--interval", type=int, default=30, help="reconcile cadence in seconds")
    p.add_argument("--plugins-dir")
    p.add_argument("--state-dir")
    args = p.parse_args(argv)
    reg = Registry(plugins_dir=args.plugins_dir, state_dir=args.state_dir)
    run(interval_seconds=args.interval, reg=reg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
