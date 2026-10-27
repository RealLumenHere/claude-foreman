#!/usr/bin/env python3
"""
lumen_watchdog.py — Homelab status monitor
Runs every 60s, writes HOMELAB_STATUS.json, sends ntfy on state changes.
Query: cat /home/lumen/scratch/HOMELAB_STATUS.json | python3 -m json.tool
"""

import json
import os
import subprocess
import time
import requests
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
STATUS_PATH = "/home/lumen/scratch/HOMELAB_STATUS.json"
NTFY_URL    = "http://localhost:8080/lumen-updates"

# Fleet roster refreshed 2026-08-15 (wq#637 bundled fix) from lumen_state.py current
# '' tailscale_ip (authoritative, supersedes hand-copying FLEET_MAP.md prose). Added:
# anubis, khepri, maat, nephthys, wadjet — all were live+deployed nodes missing from
# the 2026-06-10 roster (nephthys entirely absent is what let its 2026-08-14 outage
# go unseen by this monitor specifically, see wq#635/#637).
# KNOWN GAP: bes, sobek, seshat, gameboy, apep have TS IPs recorded only as prose in
# FLEET_MAP.md, not as lumen_state tailscale_ip facts — not mechanically addable here
# without hand-transcription risk. Left out on purpose; worth its own follow-up to
# backfill those facts into lumen_state rather than hand-copy IPs into this dict.
# "nephthys" REMOVED wq#589 (decommissioned 2026-08-22)
# "isis", "hathor", "khepri" REMOVED wq#4695 (decommissioned 2026-10-22)
# "wadjet" REMOVED wq#4695 (decommissioned 2026-10-26)
# RETIRED_NODES_PATH below is the single source of truth for decommissioned
# nodes, also read by /home/lumen/bin/maint-ops/tests/{ollama,tailscale}_canary.sh
# (wq#4695) -- recording a retirement there once is enough to stop both this
# monitor AND the canary harnesses from targeting a dead node, instead of each
# hand-tracking its own copy and drifting out of sync (the bug this ticket fixed).
DEVICES = {
    "lumen":        "100.117.66.102",
    "thor":         "100.112.151.100",
    "thoth":        "100.75.1.108",
    "3dprinter":    "100.114.82.85",
    "phone":        "100.67.171.124",
    "anubis":       "100.121.17.5",
    "maat":         "100.105.219.34",
}

RETIRED_NODES_PATH = "/home/lumen/bin/fleet-retired-nodes.json"

# aq#59: devices that sleep/roam by design — transitions logged, never alerted.
TRANSIENT_DEVICES = {"thor", "phone", "3dprinter"}

SERVICES = {
    # ghost removed 2026-06-10 — Ghost CMS permanently deleted 2026-05
    "n8n":          ("localhost", 5678),
    "litellm":      ("localhost", 4000),
    "redis":        ("localhost", 6379),
    "ntfy":         ("localhost", 8080),
    # "librechat":  ("localhost", 3080),  # removed 2026-04-21 — LibreChat deprecated
    "beszel":       ("localhost", 8090),
    # "uptime_kuma": removed 2026-07-10 — was an npx first-run wizard, unconfigured
    # since May 17, process died on its own; Gatus is the monitoring stack. (aq#59 session)
}

OLLAMA_URL  = "http://127.0.0.1:11434/api/tags"  # 2026-06-10: was retired PBox; now Lumen local Ollama
STATE_PATH  = "/home/lumen/scratch/.watchdog_prev_state.json"

# ── Helpers ───────────────────────────────────────────────────────────────────

def ping(ip, timeout=2):
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), ip],
            capture_output=True, timeout=timeout + 1
        )
        return r.returncode == 0
    except Exception:
        return False


def check_port(host, port, timeout=2):
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def check_ollama():
    try:
        r = requests.get(OLLAMA_URL, timeout=4)
        return r.status_code == 200
    except Exception:
        return False


def load_prev_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def send_ntfy(title, message, priority="default"):
    try:
        requests.post(NTFY_URL, data=message.encode(),
                      headers={"Title": title, "Priority": priority},
                      timeout=5)
    except Exception:
        pass


def check_no_retired_devices_monitored():
    """Loud startup guard (wq#4695): a retired node re-added to DEVICES (or a
    retirement recorded in RETIRED_NODES_PATH but never reflected above) must
    be caught here instead of silently pinging a dead host forever."""
    try:
        with open(RETIRED_NODES_PATH) as f:
            retired = set(json.load(f))
    except Exception as e:
        print(f"[warn] could not load {RETIRED_NODES_PATH}: {e!r}")
        return
    stale = retired & set(DEVICES)
    if stale:
        raise RuntimeError(
            f"DEVICES still monitors retired node(s) {sorted(stale)} — "
            f"remove from DEVICES in lumen_watchdog.py (see {RETIRED_NODES_PATH})")


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    device_status = {name: ping(ip) for name, ip in DEVICES.items()}
    service_status = {name: check_port(host, port)
                      for name, (host, port) in SERVICES.items()}
    ollama_ready = check_ollama()

    status = {
        "timestamp": ts,
        "devices": device_status,
        "services": service_status,
        "ollama_ready": ollama_ready,
    }

    with open(STATUS_PATH, "w") as f:
        json.dump(status, f, indent=2)

    # Alert on state changes
    prev = load_prev_state()
    alerts = []

    for name, up in device_status.items():
        prev_up = prev.get("devices", {}).get(name, True)
        # aq#59 fix: laptops/phones/printers sleep by design — their transitions
        # are not incidents. Log to stdout only, never ntfy.
        if name in TRANSIENT_DEVICES:
            if up != prev_up:
                print(f"[transient] {name} {'UP' if up else 'DOWN'} (no alert)")
            continue
        if not up and prev_up:
            alerts.append((f"Homelab: {name}", f"🔴 Device DOWN: {name} ({DEVICES[name]})"))
        elif up and not prev_up:
            alerts.append((f"Homelab: {name}", f"🟢 Device UP: {name} ({DEVICES[name]})"))

    for name, up in service_status.items():
        prev_up = prev.get("services", {}).get(name, True)
        if not up and prev_up:
            alerts.append((f"Homelab: {name}", f"🔴 Service DOWN: {name}"))
        elif up and not prev_up:
            alerts.append((f"Homelab: {name}", f"🟢 Service UP: {name}"))

    prev_ollama = prev.get("ollama_ready", True)
    if not ollama_ready and prev_ollama:
        alerts.append(("Homelab: ollama", "🔴 Local Ollama DOWN — embeddings/local inference unavailable"))
    elif ollama_ready and not prev_ollama:
        alerts.append(("Homelab: ollama", "🟢 Local Ollama UP"))

    for title, alert in alerts:
        priority = "high" if "DOWN" in alert else "default"
        # aq#59: per-subject titles so aq keys stay distinct instead of all
        # transitions collapsing into one immortal "Homelab Alert" row.
        send_ntfy(title, alert, priority)

    save_state(status)

    # Summary to stdout
    down_devices  = [k for k, v in device_status.items() if not v]
    down_services = [k for k, v in service_status.items() if not v]
    print(f"[{ts}] devices_down={down_devices} services_down={down_services} ollama={ollama_ready}")


if __name__ == "__main__":
    check_no_retired_devices_monitored()
    run()
