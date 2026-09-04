#!/usr/bin/env python3
"""notify_sinks.py — dead-man's-switch notification fan-out (wq#718 AUTO-6).

Two independent sinks for a critical dispatcher event (circuit-breaker trip today;
any future dead-man's-switch condition can reuse this). Each is optional and
best-effort by contract — neither being unreachable may ever raise, and one sink
failing must never suppress the other:

  - ntfy (NTFY_BIN): real push via the fleet's ntfy-send wrapper, same binary every
    other foreman script already uses. `notify_critical` uses priority=urgent.
  - Frank's desktop (DESKTOP_SSH_HOST): notify-send over the tailnet. Independent of
    ntfy in transport (SSH, not the ntfy HTTP origin) and in failure mode, and it
    lands in the xfce4-notifyd panel log so the alert is re-readable after the popup
    fades. Thor is a roaming laptop (overlay.yaml unreachable_ok) — asleep or
    off-tailnet is normal, not an error: same log-only-degradation contract as
    foreman_dispatcher.py's Langfuse emitter.

Both functions return a plain bool (delivered / not) instead of raising, so a caller
that must never be blocked by notification failure (circuit_breaker.disarm's fail-safe
contract) can call this and move on unconditionally.
"""
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone

NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")
DESKTOP_SSH_HOST = os.environ.get("FOREMAN_DESKTOP_SSH_HOST", "thor")


def ntfy_critical(msg, title="foreman-dispatcher"):
    try:
        p = subprocess.run([NTFY_BIN, "-p", "urgent", "-T", title, msg],
                           capture_output=True, text=True, timeout=20)
        delivered = p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        delivered = False
    # wq#1416: ntfy's own cache.db only retains ~12h — print a journald copy (stderr, so
    # it never pollutes a caller's stdout-as-JSON contract, e.g. circuit_breaker.py's
    # `test-ntfy`/`test`) of every critical publish, so vlogs-forwarder (already shipping
    # this host's journal to bes:9428 VictoriaLogs, 30d retention) keeps it queryable
    # well past that window.
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "event": "ntfy_publish", "title": title, "priority": "urgent",
                       "delivered": delivered, "msg": msg}), file=sys.stderr, flush=True)
    return delivered


def desktop_notify(summary, body, app_name="foreman-dispatcher"):
    """Best-effort desktop notification on DESKTOP_SSH_HOST. False (never raises) on any
    failure, including "not configured" (DESKTOP_SSH_HOST empty) and "host asleep or
    off-tailnet", which for a roaming laptop is the ordinary case.

    Delivery is notify-send, i.e. org.freedesktop.Notifications, and NOT a direct insert
    into ~/.cache/xfce4/notifyd/log.sqlite (what this sink used to attempt). That file is
    owned by a live xfce4-notifyd, which serves it over org.xfce.Notifyd.Log and pushes
    RowAdded to the panel plugin: a row written behind the daemon's back is invisible to
    the notification bell and contends for locks on the history Frank actually reads. The
    old insert also named columns (app_icon, time) that 0.9.x's schema does not have, so
    it could only ever return False (wq#664)."""
    if not DESKTOP_SSH_HOST:
        return False
    remote = "notify-send -a {} -u critical {} {}".format(
        shlex.quote("Lumen/" + app_name), shlex.quote(summary), shlex.quote(body))
    try:
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                            DESKTOP_SSH_HOST, remote],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0


def notify_critical(msg, title="foreman-dispatcher"):
    """Fan out to every sink; each sink's own failure is independent (see module
    docstring). Returns {'ntfy': bool, 'desktop': bool} — a caller can log which
    sinks actually delivered without either one being able to raise."""
    return {"ntfy": ntfy_critical(msg, title),
            "desktop": desktop_notify(title, msg)}
