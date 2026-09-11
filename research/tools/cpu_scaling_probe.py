#!/usr/bin/env python3
"""wq#1709 -- CPU-parallelism ceiling probe.

Companion to membw_probe.py. That probe's aggregate GB/s stops rising past a
certain worker count, and there are two very different explanations: the memory
controller is saturated (a real hardware ceiling that bounds local tok/s), or
the caller is inside a cgroup CPU quota (a sandbox artifact that says nothing
about the hardware). This probe separates them by running pure compute with no
memory traffic -- work stays in registers/L1, so anything that flattens the
curve here is CPU allocation, not bandwidth.

Compare the two curves: if this one scales linearly to N and membw_probe
flattens at M < N, M is the real memory ceiling.
"""
import argparse
import multiprocessing as mp
import time


def worker(seconds, ready, out):
    ready.wait()
    iters = 0
    x = 1.0
    t0 = time.monotonic()
    t_end = t0 + seconds
    while time.monotonic() < t_end:
        for _ in range(20000):
            x = x * 1.000001 + 1.0
        iters += 20000
    out.put((iters, time.monotonic() - t0, x))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seconds", type=float, default=3.0)
    a = ap.parse_args()

    q = mp.Queue()
    ready = mp.Barrier(a.workers)
    procs = [mp.Process(target=worker, args=(a.seconds, ready, q))
             for _ in range(a.workers)]
    for p in procs:
        p.start()
    results = [q.get() for _ in procs]
    for p in procs:
        p.join()

    rate = sum(iters / secs for iters, secs, _ in results) / 1e6
    print("workers=%d aggregate=%.1f Miter/s" % (a.workers, rate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
