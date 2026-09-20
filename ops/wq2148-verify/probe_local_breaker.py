"""wq#2148 probe: wq#2130's per-pool breaker against synthetic attempt records.

Everything lives in temp dirs and notify_sinks is stubbed, so a run writes nothing to the
live DISARM/pause files and sends no ntfy. Run: python3 ops/wq2148-verify/probe_local_breaker.py
"""
import json
import os
import sys
import tempfile
import time

d = tempfile.mkdtemp(prefix="wq2148_")
os.environ.update(FOREMAN_RUNS_ROOT=d + "/runs", FOREMAN_DISPATCH_DISARM=d + "/DISARM",
                  FOREMAN_LOCAL_PAUSE=d + "/local_pause.json",
                  FOREMAN_BACKEND_COOLDOWN=d + "/cd.json")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "dispatcher"))
import circuit_breaker as cb  # noqa: E402
import local_capacity as lc  # noqa: E402

sent = []
cb.notify_sinks.ntfy_critical = lambda msg, title="": sent.append(title)
cb.notify_sinks.notify_critical = lambda msg, title="": sent.append(title)

now = time.time()


def rec(tid, model, cls, age=60):
    p = f"{d}/runs/wq{tid}"
    os.makedirs(p, exist_ok=True)
    json.dump({"finished_at": now - age, "classification": cls, "model": model},
              open(f"{p}/attempt-1.json", "w"))


for i, m in enumerate(["hathor-llama8b@low", "nephthys-qwen14b", "local-worker", "qwen36-local",
                       "deepseek-v4-flash", "hathor-llama8b"] * 2):
    rec(100 + i, m, "OOM")
for i in range(10):
    rec(200 + i, "claude-sonnet-5", "SUCCESS")

print("cloud tripped:", cb.check_and_trip(now=now)[0], "| DISARM written:",
      os.path.exists(d + "/DISARM"))
print("local trip:", cb.check_and_trip_local(now=now)[0], "| pause active:",
      lc.read_pause(now) is not None, "| re-trip while paused:",
      cb.check_and_trip_local(now=now)[0])
print("DISARM written after local trip:", os.path.exists(d + "/DISARM"))
later = now + 61 * 60
print("pause at +61m:", lc.read_pause(later), "| re-trip at +61m:",
      cb.check_and_trip_local(now=later)[0], "(window 90m > pause 60m)")
print("ntfy sent (stubbed):", sent)
