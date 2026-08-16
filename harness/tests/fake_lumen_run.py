#!/usr/bin/env python3
"""fake `lumen-run` shim for zero-quota concurrency/spawner tests. Must be installed
under the exact name `lumen-run` on PATH (router.dispatch_cmd hardcodes the bare
command name) — see tests/test_concurrency.py / tests/test_spawner.py for how it's
wired in (prepend a tmp bin dir to PATH, this file copied/symlinked in as `lumen-run`).

Tracks "currently running" unit slugs in a JSON list at $FAKE_LUMEN_RUN_STATE (tests
pre-seed it to simulate N units already live) and appends every `start --name X` call
to $FAKE_LUMEN_RUN_LOG as one JSON line per call (tests assert stagger timing and
governor-skip behavior off this log) — never actually execs the wrapped command, so no
real claude/systemd process is touched.
"""
import json
import os
import sys
import time


def state_path():
    return os.environ.get("FAKE_LUMEN_RUN_STATE")


def load_units():
    p = state_path()
    if p and os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return []


def save_units(units):
    p = state_path()
    if p:
        with open(p, "w") as f:
            json.dump(units, f)


def log_call(args):
    p = os.environ.get("FAKE_LUMEN_RUN_LOG")
    if p:
        with open(p, "a") as f:
            f.write(json.dumps({"ts": time.time(), "args": args}) + "\n")


def main():
    args = sys.argv[1:]
    if args[:1] == ["list"]:
        for name in load_units():
            print(f"  lumen-job-{name}-{int(time.time())}.service     "
                  f"loaded active running [fake] /bin/true")
        return 0
    if args[:1] == ["start"]:
        name = None
        for i, a in enumerate(args):
            if a == "--name" and i + 1 < len(args):
                name = args[i + 1]
        log_call(args)
        if name:
            units = load_units()
            units.append(name)
            save_units(units)
        print(f"UNIT=lumen-job-{name}-{int(time.time())}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
