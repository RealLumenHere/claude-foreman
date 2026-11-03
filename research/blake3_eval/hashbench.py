#!/usr/bin/env python3
"""Single-thread digest throughput bench for wq#4831 (BLAKE3 evaluation).

Hashes one in-memory buffer with every algorithm available on this host and
reports MiB/s plus the projected wall time for a Limpet ISO of --iso-gib.
Optional backends (the `blake3` and `xxhash` PyPI modules, the `b3sum`
binary) are measured when present and reported as "unavailable" otherwise,
so the output records what the host could and could not test.

    python3 hashbench.py [--mib 512] [--iso-gib 6.3] [--json]
"""
import argparse
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import tempfile
import time


def _hashlib(name):
    return lambda buf: hashlib.new(name, buf).digest()


def _module(mod, attr):
    try:
        m = importlib.import_module(mod)
    except ImportError:
        return None
    ctor = getattr(m, attr)
    return lambda buf: ctor(buf).digest()


def backends():
    out = {
        "sha256": _hashlib("sha256"),
        "sha512": _hashlib("sha512"),
        "blake2b": _hashlib("blake2b"),
        "blake3": _module("blake3", "blake3"),
        "xxh3_128": _module("xxhash", "xxh3_128"),
    }
    return out


def time_fn(fn, buf, rounds):
    best = float("inf")
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn(buf)
        best = min(best, time.perf_counter() - t0)
    return best


def time_cli(argv, path, rounds):
    best = float("inf")
    for _ in range(rounds):
        t0 = time.perf_counter()
        subprocess.run(argv + [path], check=True, stdout=subprocess.DEVNULL)
        best = min(best, time.perf_counter() - t0)
    return best


def run(mib, iso_gib, rounds):
    buf = os.urandom(mib * 1024 * 1024)
    rows = {}
    for name, fn in backends().items():
        if fn is None:
            rows[name] = None
            continue
        rows[name] = time_fn(fn, buf, rounds)

    clis = {"sha256sum": ["sha256sum"], "gnusha256sum": ["gnusha256sum"],
            "openssl_sha256": ["openssl", "dgst", "-sha256"],
            "b3sum": ["b3sum"],
            "b3sum_1thread": ["b3sum", "--num-threads", "1"]}
    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(buf)
        f.flush()
        time_cli(["sha256sum"], f.name, 1)  # warm the page cache
        for name, argv in clis.items():
            if shutil.which(argv[0]) is None:
                rows["cli:" + name] = None
                continue
            rows["cli:" + name] = time_cli(argv, f.name, rounds)

    result = {"buffer_mib": mib, "iso_gib": iso_gib, "cpu": _cpu(),
              "nproc": os.cpu_count(), "results": {}}
    for name, secs in rows.items():
        if secs is None:
            result["results"][name] = "unavailable"
            continue
        rate = mib / secs
        result["results"][name] = {
            "mib_s": round(rate, 1),
            "iso_s": round(iso_gib * 1024 / rate, 1),
        }
    return result


def _cpu():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mib", type=int, default=512)
    ap.add_argument("--iso-gib", type=float, default=6.3)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = run(args.mib, args.iso_gib, args.rounds)
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    print(f"cpu={res['cpu']} nproc={res['nproc']} buffer={args.mib}MiB")
    for name, r in res["results"].items():
        if r == "unavailable":
            print(f"  {name:16} unavailable")
        else:
            print(f"  {name:16} {r['mib_s']:>8} MiB/s  "
                  f"{args.iso_gib}GiB ISO ~{r['iso_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
