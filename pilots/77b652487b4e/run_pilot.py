#!/usr/bin/env python3
"""Pilot 77b652487b4e — dynamic worker scaling vs fixed concurrency.

Replays the fixed trace in real time. Workers are threads whose "work" is
sleeping the task's pinned service time (I/O-bound, like a researcher waiting
on an LLM call). Baseline: 3 workers always on. Treatment: depth-proportional
scaler, 1-6 workers, 1 s poll, 1.5 s scale-up actuation delay (systemd-start
stand-in), 5-poll scale-down hysteresis, no mid-task kills.
"""
import argparse
import json
import math
import threading
import time
from collections import deque
from pathlib import Path

BASELINE_WORKERS = 3
MIN_WORKERS, MAX_WORKERS = 1, 6
POLL_S = 1.0
SCALE_UP_DELAY_S = 1.5
DOWN_HYSTERESIS_POLLS = 5
TASKS_PER_WORKER = 2


class Pilot:
    def __init__(self, tasks):
        self.tasks = tasks
        self.queue = deque()
        self.lock = threading.Lock()
        self.done = []
        self.n_tasks = len(tasks)
        self.t0 = None
        self.workers = {}  # wid -> {"thread", "stop", "started", "stopped"}
        self.next_wid = 0
        self.all_done = threading.Event()

    def now(self):
        return time.monotonic() - self.t0

    def feeder(self):
        for task in self.tasks:
            delay = task["arrival"] - self.now()
            if delay > 0:
                time.sleep(delay)
            with self.lock:
                self.queue.append({**task, "enqueued": self.now()})

    def worker_loop(self, wid, stop):
        while not stop.is_set():
            with self.lock:
                task = self.queue.popleft() if self.queue else None
            if task is None:
                if self.all_done.is_set():
                    break
                time.sleep(0.05)
                continue
            time.sleep(task["service"])
            with self.lock:
                self.done.append(
                    {
                        "id": task["id"],
                        "arrival": task["arrival"],
                        "service": task["service"],
                        "completed": self.now(),
                        "turnaround": self.now() - task["arrival"],
                        "worker": wid,
                    }
                )
                if len(self.done) == self.n_tasks:
                    self.all_done.set()
        self.workers[wid]["stopped"] = self.now()

    def start_worker(self, delay=0.0):
        wid = self.next_wid
        self.next_wid += 1
        stop = threading.Event()

        def run():
            if delay > 0:
                time.sleep(delay)
            self.workers[wid]["started"] = self.now()
            self.worker_loop(wid, stop)

        thread = threading.Thread(target=run, daemon=True)
        self.workers[wid] = {"thread": thread, "stop": stop, "started": None, "stopped": None}
        thread.start()
        return wid

    def active_ids(self):
        return [w for w, rec in self.workers.items() if rec["stopped"] is None and not rec["stop"].is_set()]

    def run_baseline(self):
        self.t0 = time.monotonic()
        for _ in range(BASELINE_WORKERS):
            self.start_worker()
        feeder = threading.Thread(target=self.feeder, daemon=True)
        feeder.start()
        self.all_done.wait()
        for rec in self.workers.values():
            rec["stop"].set()
        for rec in self.workers.values():
            rec["thread"].join()
        return {"arm": "baseline", "workers_fixed": BASELINE_WORKERS}

    def run_treatment(self):
        self.t0 = time.monotonic()
        self.start_worker()  # floor of 1, up from t=0
        feeder = threading.Thread(target=self.feeder, daemon=True)
        feeder.start()
        low_polls = 0
        samples = []
        while not self.all_done.is_set():
            time.sleep(POLL_S)
            with self.lock:
                depth = len(self.queue)
            desired = max(MIN_WORKERS, min(MAX_WORKERS, math.ceil(depth / TASKS_PER_WORKER)))
            active = self.active_ids()
            samples.append({"t": round(self.now(), 2), "depth": depth, "active": len(active), "desired": desired})
            if desired > len(active):
                low_polls = 0
                for _ in range(desired - len(active)):
                    self.start_worker(delay=SCALE_UP_DELAY_S)
            elif desired < len(active):
                low_polls += 1
                if low_polls >= DOWN_HYSTERESIS_POLLS:
                    for wid in sorted(active, reverse=True)[: len(active) - desired]:
                        self.workers[wid]["stop"].set()
                    low_polls = 0
            else:
                low_polls = 0
        for rec in self.workers.values():
            rec["stop"].set()
        for rec in self.workers.values():
            rec["thread"].join()
        return {"arm": "treatment", "scaler_samples": samples}

    def metrics(self):
        end = max(d["completed"] for d in self.done)
        turnarounds = sorted(d["turnaround"] for d in self.done)
        idx = min(len(turnarounds) - 1, math.ceil(0.95 * len(turnarounds)) - 1)
        worker_seconds = 0.0
        events = []
        for rec in self.workers.values():
            started = rec["started"]
            if started is None:
                continue
            stopped = min(rec["stopped"] if rec["stopped"] is not None else end, end)
            worker_seconds += max(0.0, stopped - started)
            events.append((started, 1))
            events.append((stopped, -1))
        peak = cur = 0
        for _, delta in sorted(events):
            cur += delta
            peak = max(peak, cur)
        return {
            "tasks": len(self.done),
            "makespan_s": round(end, 2),
            "p50_turnaround_s": round(turnarounds[len(turnarounds) // 2], 2),
            "p95_turnaround_s": round(turnarounds[idx], 2),
            "mean_turnaround_s": round(sum(turnarounds) / len(turnarounds), 2),
            "max_turnaround_s": round(turnarounds[-1], 2),
            "worker_seconds": round(worker_seconds, 1),
            "peak_workers": peak,
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["baseline", "treatment"], required=True)
    ap.add_argument("--trace", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    trace = json.loads(Path(args.trace).read_text())
    pilot = Pilot(trace["tasks"])
    meta = pilot.run_baseline() if args.arm == "baseline" else pilot.run_treatment()
    result = {**meta, "metrics": pilot.metrics(), "per_task": sorted(pilot.done, key=lambda d: d["id"])}
    Path(args.out).write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({"arm": args.arm, **result["metrics"]}))


if __name__ == "__main__":
    main()
