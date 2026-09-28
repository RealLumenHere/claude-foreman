#!/usr/bin/env python3
"""wq#2880 acceptance probe for Lumen's I217-LM. Read-only, no root needed.

Exits 0 only when every criterion holds:
  1. /sys/bus/pci/devices/0000:00:19.0/driver -> e1000e
  2. the device's netdev is enp0s25 with MAC 64:00:6a:58:fc:d8 (never spoofed)
  3. `systemctl is-enabled nic-park.service` prints not-found
  4. ens1 still carries the default route at metric 100

Before the fix it fails on 1-3, which is the point: a probe that cannot fail
proves nothing (wq#2630).
"""
import argparse
import os
import subprocess
import sys

PCI_ADDR = "0000:00:19.0"
DRIVER = "e1000e"
IFNAME = "enp0s25"
MAC = "64:00:6a:58:fc:d8"
PRIMARY = "ens1"
PRIMARY_METRIC = 100


def check_driver(sysfs):
    link = os.path.join(sysfs, "bus/pci/devices", PCI_ADDR, "driver")
    if not os.path.exists(link):
        return False, "driver: UNBOUND"
    name = os.path.basename(os.path.realpath(link))
    return name == DRIVER, f"driver: {name}"


def check_netdev(sysfs):
    net = os.path.join(sysfs, "bus/pci/devices", PCI_ADDR, "net")
    names = sorted(os.listdir(net)) if os.path.isdir(net) else []
    if names != [IFNAME]:
        return False, f"netdev: {names or 'none'}"
    with open(os.path.join(net, IFNAME, "address")) as f:
        mac = f.read().strip()
    return mac == MAC, f"netdev: {IFNAME} {mac}"


def check_unit(is_enabled):
    state = is_enabled("nic-park.service")
    return state == "not-found", f"nic-park.service: {state}"


def check_route(route_text):
    """/proc/net/route: default route rows have Destination and Mask 00000000."""
    defaults = []
    for line in route_text.splitlines()[1:]:
        f = line.split()
        if len(f) >= 8 and f[1] == "00000000" and f[7] == "00000000":
            defaults.append((f[0], int(f[6])))
    ok = (PRIMARY, PRIMARY_METRIC) in defaults and min(m for _, m in defaults) == PRIMARY_METRIC
    return ok, f"default routes: {defaults}"


def systemctl_is_enabled(unit):
    r = subprocess.run(["systemctl", "is-enabled", unit], capture_output=True, text=True)
    return (r.stdout.strip() or r.stderr.strip()).splitlines()[0]


def run(sysfs, route_text, is_enabled):
    results = [check_driver(sysfs), check_netdev(sysfs), check_unit(is_enabled),
               check_route(route_text)]
    for ok, msg in results:
        print(("PASS " if ok else "FAIL ") + msg)
    return all(ok for ok, _ in results)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sysfs", default="/sys")
    ap.add_argument("--route", default="/proc/net/route")
    args = ap.parse_args()
    with open(args.route) as f:
        route_text = f.read()
    sys.exit(0 if run(args.sysfs, route_text, systemctl_is_enabled) else 1)


if __name__ == "__main__":
    main()
