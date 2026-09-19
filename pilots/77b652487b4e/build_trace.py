#!/usr/bin/env python3
"""Build the fixed arrival trace for pilot 77b652487b4e.

Deterministic: seed 1920. Writes trace.json (sorted by arrival) and
trace.json.sha256. Both pilot arms replay this exact trace.
"""
import hashlib
import json
import random
from pathlib import Path

SEED = 1920
BURST_STARTS = [0.0, 55.0, 110.0]
BURST_SIZE = 40
TRICKLE_PERIOD = 5.0
TRICKLE_END = 145.0
SERVICE_MIN, SERVICE_MAX = 0.5, 1.5


def main() -> None:
    rng = random.Random(SEED)
    tasks = []
    for start in BURST_STARTS:
        for _ in range(BURST_SIZE):
            tasks.append({"arrival": start + rng.uniform(0.0, 2.0)})
    t = 0.0
    while t <= TRICKLE_END:
        tasks.append({"arrival": t})
        t += TRICKLE_PERIOD
    tasks.sort(key=lambda x: x["arrival"])
    for i, task in enumerate(tasks):
        task["id"] = i
        task["service"] = round(rng.uniform(SERVICE_MIN, SERVICE_MAX), 3)
        task["arrival"] = round(task["arrival"], 3)

    out = Path(__file__).parent / "trace.json"
    payload = json.dumps({"seed": SEED, "tasks": tasks}, indent=1)
    out.write_text(payload + "\n")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (Path(__file__).parent / "trace.json.sha256").write_text(digest + "\n")
    print(f"{len(tasks)} tasks, sha256 {digest}")


if __name__ == "__main__":
    main()
