#!/usr/bin/env python3
"""notify_sinks.py — dead-man's-switch notification fan-out (wq#718 AUTO-6).

Two independent sinks for a critical dispatcher event (circuit-breaker trip today;
any future dead-man's-switch condition can reuse this). Each is optional and
best-effort by contract — neither being unreachable may ever raise, and one sink
failing must never suppress the other:

  - ntfy (NTFY_BIN): real push via the fleet's ntfy-send wrapper, same binary every
    other foreman script already uses. `notify_critical` uses priority=urgent.
  - xfce4-notifyd sqlite log (XFCE_NOTIFYD_DB): Thor's desktop already keeps a
    notification history in this format (table `notifications(app_name, app_icon,
    summary, body, time)` — xfce4-notifyd's own log-sqlite schema). Unset/absent by
    default: this box has no Thor mount to write through, and the real path is
    Frank's to supply once known (FOREMAN_XFCE_NOTIFYD_DB). Same log-only-degradation
    contract as foreman_dispatcher.py's Langfuse emitter — absence is normal, not an
    error.

Both functions return a plain bool (delivered / not) instead of raising, so a caller
that must never be blocked by notification failure (circuit_breaker.disarm's fail-safe
contract) can call this and move on unconditionally.
"""
import os
import sqlite3
import subprocess
import time

NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")
XFCE_NOTIFYD_DB = os.environ.get("FOREMAN_XFCE_NOTIFYD_DB", "")


def ntfy_critical(msg, title="foreman-dispatcher"):
    try:
        p = subprocess.run([NTFY_BIN, "-p", "urgent", "-T", title, msg],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0


def xfce_notifyd_log(app_name, summary, body):
    """Best-effort insert into Thor's xfce4-notifyd sqlite log. False (never raises) on
    any failure, including "not configured" (XFCE_NOTIFYD_DB unset) and "configured but
    unreachable" (e.g. a stale mount path) — this sink is a nice-to-have, never
    load-bearing."""
    if not XFCE_NOTIFYD_DB or not os.path.exists(XFCE_NOTIFYD_DB):
        return False
    try:
        conn = sqlite3.connect(XFCE_NOTIFYD_DB, timeout=5)
        try:
            conn.execute(
                "INSERT INTO notifications (app_name, app_icon, summary, body, time) "
                "VALUES (?, ?, ?, ?, ?)",
                (app_name, "", summary, body, int(time.time())))
            conn.commit()
        finally:
            conn.close()
        return True
    except (sqlite3.Error, OSError):
        return False


def notify_critical(msg, title="foreman-dispatcher"):
    """Fan out to every sink; each sink's own failure is independent (see module
    docstring). Returns {'ntfy': bool, 'xfce_notifyd': bool} — a caller can log which
    sinks actually delivered without either one being able to raise."""
    return {"ntfy": ntfy_critical(msg, title),
            "xfce_notifyd": xfce_notifyd_log(title, title, msg)}
