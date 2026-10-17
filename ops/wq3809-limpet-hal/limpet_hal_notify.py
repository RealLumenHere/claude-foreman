#!/usr/bin/env python3
"""limpet-hal-notify — udev RUN+= helper that sends a device event to the
HAL daemon via Unix domain socket.

Called by the udev rule (90-limpet.rules) on USB add/remove events.
Designed to be fast and non-blocking: sends a single JSON line and exits.
If the daemon socket is not available, exits silently (fail-open for udev;
the daemon will do a full scan on startup anyway).

Usage (from udev rule):
  RUN+="/usr/lib/limpet/limpet-hal-notify add $devpath $attr{idVendor} $attr{idProduct}"
  RUN+="/usr/lib/limpet/limpet-hal-notify remove $devpath"
"""
from __future__ import annotations

import json
import os
import socket
import sys

_DEFAULT_HAL_SOCKET = "/run/limpet/hal.sock"


def send_event(action: str, devpath: str, vid: str = "", pid: str = "") -> bool:
    """Send a device event to the HAL daemon. Returns True if delivered."""
    event = {
        "action": action,
        "devpath": devpath,
        "vid": vid.lower(),
        "pid": pid.lower(),
    }
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.settimeout(1.0)
        sock_path = os.environ.get("LIMPET_HAL_SOCKET", _DEFAULT_HAL_SOCKET)
        sock.sendto(json.dumps(event).encode("utf-8"), sock_path)
        sock.close()
        return True
    except (OSError, socket.error):
        # Daemon not running or socket not available — fail silently.
        # udev helpers must not block or fail loudly.
        return False


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 2:
        return 0  # silent — udev helper, don't spam syslog

    action = args[0]  # "add" or "remove"
    devpath = args[1]
    vid = args[2] if len(args) > 2 else ""
    pid = args[3] if len(args) > 3 else ""

    send_event(action, devpath, vid, pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
