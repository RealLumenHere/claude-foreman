#!/usr/bin/env python3
"""Detached launcher for the Remote Desktop Commander device agent (wq#4330).

Spawns `npx @wonderwhy-er/desktop-commander@latest remote` in its own session so
it survives the worker session. Output -> agent.log, pid -> agent.pid.
"""
import os
import subprocess
import sys

RUN_DIR = "/tmp/fworker-wq4330/rdc"
LOG = os.path.join(RUN_DIR, "agent.log")
PID = os.path.join(RUN_DIR, "agent.pid")

os.makedirs(RUN_DIR, exist_ok=True)

if os.path.exists(PID):
    with open(PID) as f:
        old = f.read().strip()
    if old and os.path.exists(f"/proc/{old}"):
        print(f"already running pid={old}")
        sys.exit(0)

env = dict(os.environ)
# headless box: stop the agent trying to open a browser
env["BROWSER"] = "true"  # `true` binary: no-op success

with open(LOG, "ab") as log:
    proc = subprocess.Popen(
        ["npx", "-y", "@wonderwhy-er/desktop-commander@latest", "remote"],
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )

with open(PID, "w") as f:
    f.write(str(proc.pid))
print(f"started pid={proc.pid} log={LOG}")
