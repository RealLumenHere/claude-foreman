#!/usr/bin/env python3
"""wq#3289 — find the top swapper and recycle stale swap.

2026-10-01 audit wall-5: `free -h` showed Swap 11-12Gi used / 23Gi, with ~47-49Gi
of Mem available. vm.swappiness is already 10 (wq#15/17); the remaining used
swap is cold pages parked there from an earlier memory-pressure spike that
never got paged back in now that pressure is gone. `swapoff -a` forces the
kernel to pull every swapped page back into RAM, `swapon -a` re-enables swap
empty — the standard "recycle" for this, safe only while MemAvailable
comfortably exceeds SwapUsed (see `is_safe_to_recycle`).

Subcommands:
  top [N]      print the N processes (default 10) with the most VmSwap, read
               from /proc/<pid>/status. No root required.
  check        print MemAvailable / SwapUsed from /proc/meminfo and whether a
               recycle is currently safe. No root required.
  recycle      run the `check` precondition, then either print the dry-run
               plan (default) or actually run `swapoff -a && swapon -a`
               (--execute). Actually swapping requires root — run this
               subcommand via `lumen-run start --root --` per repo convention
               (see ops/wq2094-root-migration), never as a foreground sudo.
"""
import argparse
import glob
import re
import subprocess
import sys


def parse_meminfo(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"(\w+):\s+(\d+)\s*kB", line)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def swap_used_kb(meminfo):
    return meminfo["SwapTotal"] - meminfo["SwapFree"]


def is_safe_to_recycle(meminfo):
    """MemAvailable must exceed SwapUsed: swapoff has to fit every swapped
    page back into RAM at once, so there must be room for all of it, not just
    more free RAM than swap in general."""
    return meminfo["MemAvailable"] >= swap_used_kb(meminfo)


def scan_vmswap(proc_root="/proc"):
    """Returns [(vmswap_kb, pid, name), ...] for every process with VmSwap > 0."""
    results = []
    for status_path in glob.glob(f"{proc_root}/[0-9]*/status"):
        pid = status_path.split("/")[-2]
        try:
            with open(status_path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue  # process exited mid-scan
        m = re.search(r"VmSwap:\s+(\d+) kB", text)
        if not m or int(m.group(1)) == 0:
            continue
        name_m = re.search(r"Name:\s+(\S+)", text)
        results.append((int(m.group(1)), pid, name_m.group(1) if name_m else "?"))
    results.sort(reverse=True)
    return results


def cmd_top(args):
    for kb, pid, name in scan_vmswap()[: args.n]:
        print(f"{kb / 1024 / 1024:.2f}G\tpid={pid}\t{name}")
    return 0


def cmd_check(args):
    meminfo = parse_meminfo(open("/proc/meminfo", encoding="utf-8").read())
    used = swap_used_kb(meminfo)
    avail = meminfo["MemAvailable"]
    safe = is_safe_to_recycle(meminfo)
    print(f"SwapUsed={used / 1024 / 1024:.2f}G  MemAvailable={avail / 1024 / 1024:.2f}G  safe={safe}")
    return 0 if safe else 1


def cmd_recycle(args):
    meminfo = parse_meminfo(open("/proc/meminfo", encoding="utf-8").read())
    if not is_safe_to_recycle(meminfo) and not args.force:
        used = swap_used_kb(meminfo) / 1024 / 1024
        avail = meminfo["MemAvailable"] / 1024 / 1024
        print(
            f"REFUSING: MemAvailable ({avail:.2f}G) < SwapUsed ({used:.2f}G); "
            "swapoff could OOM. Pass --force to override.",
            file=sys.stderr,
        )
        return 1
    if not args.execute:
        print("DRY RUN (pass --execute to actually run): swapoff -a && swapon -a")
        return 0
    subprocess.run(["swapoff", "-a"], check=True)
    subprocess.run(["swapon", "-a"], check=True)
    print("recycled: swapoff -a && swapon -a completed")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    top_p = sub.add_parser("top")
    top_p.add_argument("n", type=int, nargs="?", default=10)
    top_p.set_defaults(func=cmd_top)

    check_p = sub.add_parser("check")
    check_p.set_defaults(func=cmd_check)

    recycle_p = sub.add_parser("recycle")
    recycle_p.add_argument("--execute", action="store_true")
    recycle_p.add_argument("--force", action="store_true")
    recycle_p.set_defaults(func=cmd_recycle)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
