#!/usr/bin/env python3
"""wq#5321: adversarial probe of wq#5273 (PR #2748, 5bdd3605) provider-watch --verify.

Reuses the PR's own hermetic harness (ops/tests/test_wq5273_provider_watch_intended_off.run:
temp copy of the bundle, systemctl shim, BIN_DST pinned to a temp file) from THIS checkout,
so the input is pinned to the committed tree. Probes inputs from outside the PR's tested
population (disabled/enabled x active/inactive):

  1. systemctl unreachable (prints nothing, exits 1)  -> what verdict?
  2. enabled-runtime + inactive                         -> what verdict?
  3. masked + inactive                                   -> intended-off, should pass
  4. enabled-runtime + active                            -> must not pass

Prints one line per case: name, rc, VERIFY line. Exit 0 always; the report reads the table.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "ops", "tests"))
import test_wq5273_provider_watch_intended_off as t  # noqa: E402


def unreachable_shim(enabled, active):
    return '#!/bin/sh\necho "$@" >> "$SHIM_LOG"\nexit 1\n'


def verdict(out):
    lines = [ln for ln in out.splitlines() if ln.startswith(("VERIFY", "MISS", "OFF"))]
    return " | ".join(lines)


def main():
    orig = t.shim
    t.shim = unreachable_shim
    try:
        rc, out, _ = t.run(["--verify"], payload="same")
    finally:
        t.shim = orig
    print(f"unreachable-systemctl rc={rc} :: {verdict(out)}")
    for en, act in (("enabled-runtime", "inactive"), ("masked", "inactive"),
                    ("enabled-runtime", "active")):
        rc, out, _ = t.run(["--verify"], enabled=en, active=act, payload="same")
        print(f"{en}/{act} rc={rc} :: {verdict(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
