#!/usr/bin/env python3
"""wq#1709 -- aggregate memory-bandwidth probe (STREAM-copy analogue).

Local CPU token-generation speed for a quantised LLM is bounded by how fast the
box can stream weight bytes, not by FLOPs. Every tok/s figure in
LOCAL_MODEL_STRATEGY.md is derived from this number, so it is measured rather
than assumed.

Each worker process copies a private buffer repeatedly; the score is the summed
bytes moved (read+write) divided by wall time. Run with a worker count at or
below the socket's channel count x2 -- workers past memory saturation add
scheduler noise, not bandwidth.
"""
import argparse
import multiprocessing as mp
import time


def worker(mb, seconds, ready, out):
    src = bytearray(mb * 1024 * 1024)
    dst = bytearray(mb * 1024 * 1024)
    mv_s, mv_d = memoryview(src), memoryview(dst)
    mv_d[:] = mv_s                      # fault both buffers in before timing
    # Allocation and first-touch are multi-second at high worker counts; waiting
    # here keeps that cost out of every worker's measured window, otherwise the
    # probe reports slower aggregates the more workers you add.
    ready.wait()
    moved = 0
    t0 = time.monotonic()
    t_end = t0 + seconds
    while time.monotonic() < t_end:
        mv_d[:] = mv_s
        moved += mb * 1024 * 1024 * 2   # one read + one write
    out.put((moved, time.monotonic() - t0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--mb", type=int, default=256, help="buffer MiB per worker (allocated twice)")
    ap.add_argument("--seconds", type=float, default=4.0)
    a = ap.parse_args()

    q = mp.Queue()
    ready = mp.Barrier(a.workers)
    procs = [mp.Process(target=worker, args=(a.mb, a.seconds, ready, q))
             for _ in range(a.workers)]
    for p in procs:
        p.start()
    results = [q.get() for _ in procs]
    for p in procs:
        p.join()

    gbs = sum(moved / secs for moved, secs in results) / 1e9
    print("workers=%d buf=%dMiB aggregate=%.1f GB/s"
          % (a.workers, a.mb, gbs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
