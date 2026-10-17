#!/usr/bin/env python3
"""hal_daemon.py — LIMPET HAL daemon (wq#4112).

Listens on a Unix domain socket for device events from limpet-hal-notify,
matches them against the hardware plugin registry (hal.py), maintains a live
device inventory, and publishes change events to connected subscribers.

Architecture:
  udev -> limpet-hal-notify -> [/run/limpet/hal.sock] -> hal_daemon
                                                            |
                                                            +-> /var/lib/limpet/hardware.json
                                                            +-> subscriber sockets (MCP meta-server)

Design choices:
  - SOCK_DGRAM for the udev input: non-blocking, no connection state, fits
    udev's fire-and-forget RUN+= model.
  - Full lsusb rescan on startup to catch devices plugged in before the daemon.
  - Inventory written atomically (tmp+rename) on every change.
  - Subscriber notification via a separate SOCK_STREAM socket at
    /run/limpet/hal-events.sock (one JSON line per event).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import select
import signal
import socket
import sys
import time

# hal.py lives in the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hal  # noqa: E402

HAL_SOCKET = os.environ.get("LIMPET_HAL_SOCKET", "/run/limpet/hal.sock")
EVENT_SOCKET = os.environ.get("LIMPET_HAL_EVENT_SOCKET", "/run/limpet/hal-events.sock")
INVENTORY_PATH = os.environ.get("LIMPET_INVENTORY", "/var/lib/limpet/hardware.json")
RUNTIME_DIR = "/run/limpet"

log = logging.getLogger("limpet-hald")


class HalDaemon:
    """The HAL daemon: receives udev events, maintains inventory, notifies subscribers."""

    def __init__(self):
        self.inventory: dict[str, dict] = {}  # keyed by devpath
        self.subscribers: list[socket.socket] = []
        self.running = True
        self._input_sock: socket.socket | None = None
        self._event_sock: socket.socket | None = None

    def _setup_sockets(self) -> None:
        os.makedirs(RUNTIME_DIR, exist_ok=True)

        # Input socket (DGRAM — receives from limpet-hal-notify)
        for path in (HAL_SOCKET, EVENT_SOCKET):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

        self._input_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._input_sock.bind(HAL_SOCKET)
        self._input_sock.setblocking(False)
        os.chmod(HAL_SOCKET, 0o666)  # udev runs as root, but be permissive

        # Event socket (STREAM — subscribers connect here)
        self._event_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._event_sock.bind(EVENT_SOCKET)
        self._event_sock.listen(8)
        self._event_sock.setblocking(False)
        os.chmod(EVENT_SOCKET, 0o666)

    def _initial_scan(self) -> None:
        """Full lsusb scan on startup to catch devices already plugged in."""
        detected = hal.detect()
        for entry in detected:
            devpath = entry.get("device_node", "unknown")
            self.inventory[devpath] = entry
        self._write_inventory()
        log.info("initial scan: %d device(s) detected", len(detected))

    def _write_inventory(self) -> None:
        """Atomically write the current inventory to disk."""
        os.makedirs(os.path.dirname(INVENTORY_PATH), exist_ok=True)
        tmp = INVENTORY_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(list(self.inventory.values()), f, indent=2)
            f.write("\n")
        os.replace(tmp, INVENTORY_PATH)

    def _notify_subscribers(self, event: dict) -> None:
        """Send an event to all connected subscribers, removing dead ones."""
        msg = json.dumps(event).encode("utf-8") + b"\n"
        dead = []
        for sub in self.subscribers:
            try:
                sub.sendall(msg)
            except (BrokenPipeError, ConnectionResetError, OSError):
                dead.append(sub)
        for sub in dead:
            self.subscribers.remove(sub)
            try:
                sub.close()
            except OSError:
                pass

    def _handle_device_event(self, data: bytes) -> None:
        """Process one event from limpet-hal-notify."""
        try:
            event = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning("malformed event, ignoring")
            return

        action = event.get("action", "")
        devpath = event.get("devpath", "")
        vid = event.get("vid", "")
        pid = event.get("pid", "")

        if action == "add":
            # Re-run full detection to find the new device
            detected = hal.detect()
            changed = False
            for entry in detected:
                node = entry.get("device_node", "unknown")
                if node not in self.inventory:
                    self.inventory[node] = entry
                    changed = True
                    log.info("device added: %s (%s)", entry["name"], entry["usb_id"])
                    self._notify_subscribers({
                        "type": "device_added",
                        "device": entry,
                        "timestamp": time.time(),
                    })
            if changed:
                self._write_inventory()

        elif action == "remove":
            # Check which devices are no longer present
            current = hal.detect()
            current_nodes = {e.get("device_node", "unknown") for e in current}
            removed = []
            for node, entry in list(self.inventory.items()):
                if node not in current_nodes:
                    removed.append(entry)
                    del self.inventory[node]
            if removed:
                self._write_inventory()
                for entry in removed:
                    log.info("device removed: %s", entry["name"])
                    self._notify_subscribers({
                        "type": "device_removed",
                        "device": entry,
                        "timestamp": time.time(),
                    })

    def run(self) -> None:
        """Main event loop."""
        self._setup_sockets()
        self._initial_scan()

        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))

        log.info("limpet-hald listening on %s", HAL_SOCKET)

        while self.running:
            readable = [self._input_sock, self._event_sock]
            try:
                ready, _, _ = select.select(readable, [], [], 1.0)
            except (OSError, ValueError):
                break

            for sock in ready:
                if sock is self._input_sock:
                    try:
                        data, _ = self._input_sock.recvfrom(4096)
                        self._handle_device_event(data)
                    except (OSError, BlockingIOError):
                        pass
                elif sock is self._event_sock:
                    try:
                        conn, _ = self._event_sock.accept()
                        conn.setblocking(False)
                        self.subscribers.append(conn)
                        log.info("new subscriber connected")
                    except (OSError, BlockingIOError):
                        pass

        # Cleanup
        for path in (HAL_SOCKET, EVENT_SOCKET):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        log.info("limpet-hald stopped")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="limpet-hald",
        description="LIMPET Hardware Abstraction Layer daemon")
    parser.add_argument("command", choices=["run", "status"],
                        help="run = start daemon, status = check if running")
    parser.add_argument("--foreground", action="store_true",
                        help="run in foreground (for systemd Type=simple)")
    args = parser.parse_args(argv)

    if args.command == "status":
        if os.path.exists(HAL_SOCKET):
            print("limpet-hald: socket exists, daemon likely running")
            return 0
        else:
            print("limpet-hald: not running")
            return 1

    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s: %(message)s",
        stream=sys.stdout,
    )

    daemon = HalDaemon()
    daemon.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
