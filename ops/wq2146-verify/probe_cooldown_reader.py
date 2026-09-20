#!/usr/bin/env python3
"""wq#2146 probe: does self_heal.backend_in_cooldown agree with local_capacity.cooled_down_backends
on every shape runtime/backend_cooldown.json documents? Run from the repo root:
    python3 ops/wq2146-verify/probe_cooldown_reader.py
Exit 1 when the two readers disagree on any row (the wq#2129 defect)."""
import json
import os
import sys
import tempfile
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "dispatcher"))
path = os.path.join(tempfile.mkdtemp(), "backend_cooldown.json")
os.environ["FOREMAN_BACKEND_COOLDOWN"] = path
import local_capacity  # noqa: E402
import self_heal  # noqa: E402

NAME = "hathor-llama8b"
now = time.time()
ROWS = [
    ("bare epoch, lapsed", {NAME: now - 3600}),
    ("bare epoch, future", {NAME: now + 3600}),
    ("dict until, lapsed", {NAME: {"until": now - 3600}}),
    ("dict until, future", {NAME: {"until": now + 3600}}),
    ("key with @effort, lapsed", {NAME + "@low": {"until": now - 3600}}),
    ("key with @effort, future", {NAME + "@low": {"until": now + 3600}}),
]

bad = 0
for label, data in ROWS:
    with open(path, "w") as f:
        json.dump(data, f)
    heal = self_heal.backend_in_cooldown(NAME)
    cap = NAME in local_capacity.cooled_down_backends(path=path)
    verdict = "ok      " if heal == cap else "MISMATCH"
    bad += heal != cap
    print(f"{verdict} {label:26} self_heal={heal!s:5} local_capacity={cap}")
sys.exit(1 if bad else 0)
