#!/usr/bin/env python3
"""checks_stream.py — S1..S4, the LPP stream surface. wq#1252.

S1  pokemon.lumennode.uk answers AND its event feed is advancing
S2  lumen-pokemon-live is active and has logged recently
S3  cloudflared is up and still has an ingress rule for the pokemon host
S4  :8871 answers on loopback

S1 deliberately does not stop at "HTTP 200". lumen-lpp-brb.service serves a
static BRB splash on the same port whenever the live agent is down
(pokemon/deploy/lumen-lpp-brb.service:2), so a 200 is exactly what a dead
stream looks like from outside. Freshness comes from /events.json, whose every
entry carries a `t` ISO stamp (pokemon/src/stream.ts:408).
"""
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from guardian import OK, RED, UNCONFIGURED, Result, run_heal


def _cfg(cfg):
    return cfg["stream"]


# Cloudflare answers 403 to urllib's default "Python-urllib/3.x" User-Agent on
# this zone — verified 2026-09-02: identical request, UA set = 200, UA unset =
# 403. Without an explicit UA S1 would page every 2 minutes on a perfectly
# healthy stream.
USER_AGENT = "lumen-public-guardian/1.0 (+wq1252)"


def _fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-store",
                                               "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(200_000)


def newest_event_age(payload, now=None):
    """Seconds since the newest event in an /events.json body, or None if the
    body is not the live agent's feed (a BRB splash, an error page, an empty
    buffer). Pure — the freshness rule is unit-tested without a server."""
    now = now if now is not None else time.time()
    try:
        events = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(events, list) or not events:
        return None
    newest = None
    for ev in events:
        stamp = ev.get("t") if isinstance(ev, dict) else None
        if not stamp:
            continue
        try:
            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        ts = parsed.timestamp()
        newest = ts if newest is None else max(newest, ts)
    return None if newest is None else now - newest


def _events_check(check_id, url, limit_s, what):
    try:
        status, body = _fetch(url.rstrip("/") + "/events.json")
    except urllib.error.HTTPError as exc:
        return Result(check_id, RED,
                      f"{what} /events.json returned HTTP {exc.code} — the live "
                      f"agent is not serving (BRB splash or nothing on the port)")
    except (urllib.error.URLError, OSError) as exc:
        return Result(check_id, RED, f"{what} unreachable: {exc}")
    if status != 200:
        return Result(check_id, RED, f"{what} /events.json HTTP {status}")
    age = newest_event_age(body)
    if age is None:
        return Result(check_id, RED,
                      f"{what} answered 200 but the body is not a live event feed "
                      f"— almost certainly the BRB splash while the agent is down",
                      {"body_head": body[:80].decode("utf-8", "replace")})
    if age > limit_s:
        return Result(check_id, RED,
                      f"{what} event feed is stale: newest event {age:.0f}s old "
                      f"(limit {limit_s}s) — the stream is up but frozen",
                      {"age_s": int(age)})
    return Result(check_id, OK, f"{what} fresh, newest event {age:.0f}s old")


def check_s1_public(cfg):
    s = _cfg(cfg)
    return _events_check("S1-public", s["public_url"],
                         s.get("freshness_s", 600), "public stream")


def check_s4_local(cfg):
    s = _cfg(cfg)
    return _events_check("S4-local", s["local_url"],
                         s.get("freshness_s", 600), "local :8871")


# --------------------------------------------------------------------------
# systemd


def systemctl(args, timeout=20):
    try:
        p = subprocess.run(["systemctl"] + args, capture_output=True,
                           text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return p.returncode, (p.stdout + p.stderr).strip()


def journal_age_s(unit, now=None, timeout=20):
    """Seconds since the unit's newest journal line; None if it has none."""
    try:
        p = subprocess.run(
            ["journalctl", "-u", unit, "-n", "1", "-o", "json", "--no-pager"],
            capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = p.stdout.strip().splitlines()
    if not line:
        return None
    try:
        usec = int(json.loads(line[-1])["__REALTIME_TIMESTAMP"])
    except (ValueError, KeyError):
        return None
    return (now if now is not None else time.time()) - usec / 1_000_000


def check_s2_unit(cfg):
    """active + evidence of progress.

    The ticket asked for "active + journal line within 15 min". Measured on
    2026-09-02, the live agent logs on save/resume only — 09:44, 10:26, 11:07,
    i.e. roughly 40-minute gaps while it was perfectly healthy. A 15-minute
    journal threshold on its own pages every 2 minutes on a working stream, so
    a quiet journal is only RED when the local event feed is ALSO not moving.
    Either signal advancing proves the agent loop is alive; neither advancing
    is a genuine stall.
    """
    s = _cfg(cfg)
    unit = s["unit"]
    rc, out = systemctl(["is-active", unit])
    if rc != 0 or out.strip() != "active":
        return Result("S2-unit", RED, f"{unit} is {out.strip() or 'unknown'}")
    age = journal_age_s(unit)
    limit = s.get("journal_s", 900)
    if age is not None and age <= limit:
        return Result("S2-unit", OK, f"{unit} active, last log {age:.0f}s ago")

    feed = check_s4_local(cfg)
    quiet = "no journal lines" if age is None else f"journal {age:.0f}s old"
    if feed.status == OK:
        return Result("S2-unit", OK,
                      f"{unit} active, {quiet} (>{limit}s) but the local event "
                      f"feed is advancing — {feed.detail}")
    return Result("S2-unit", RED,
                  f"{unit} is active but stalled: {quiet} and the local feed is "
                  f"not advancing either ({feed.detail})",
                  {"journal_age_s": None if age is None else int(age)})


def check_s3_tunnel(cfg):
    s = _cfg(cfg)
    unit = s["cloudflared_unit"]
    rc, out = systemctl(["is-active", unit])
    if rc != 0 or out.strip() != "active":
        return Result("S3-tunnel", RED,
                      f"{unit} is {out.strip() or 'unknown'} — every *.lumennode.uk "
                      f"public hostname is down, not just the stream")
    conf = s.get("cloudflared_config")
    if not conf or not os.path.exists(conf):
        return Result("S3-tunnel", UNCONFIGURED,
                      f"cloudflared is active but {conf} is unreadable from here, "
                      f"so the ingress rule cannot be verified")
    with open(conf) as f:
        text = f.read()
    host = s["host"]
    if not re.search(r"hostname:\s*[\"']?" + re.escape(host), text):
        return Result("S3-tunnel", RED,
                      f"no ingress rule for {host} in {conf} — the tunnel is up but "
                      f"will not route the stream",
                      {"config": conf})
    return Result("S3-tunnel", OK, f"{unit} active, ingress for {host} present")


# --------------------------------------------------------------------------
# heals — the ladder from the ticket: live agent first, tunnel second, then fire


def heal_restart_live(cfg, dry_run=False):
    return run_heal(["sudo", "/usr/bin/systemctl", "restart", _cfg(cfg)["unit"]],
                    timeout=120, dry_run=dry_run)


def heal_restart_cloudflared(cfg, dry_run=False):
    """Files a restart request with cfd-actuator, the one owner of cloudflared
    restarts (wq#1652 stage 3). It serves the request on its next drain tick
    (<=60 s, inside this timer's 2 min cadence) through detached systemd-run,
    behind holddown, slock and a cooldown shared with the healer and wadjet."""
    return run_heal(["/home/lumen/bin/cfd-actuator", "request",
                     "--from", "public-guardian", "--kind", "restart",
                     "--reason", f"heal ladder: {_cfg(cfg)['cloudflared_unit']} stream red"],
                    timeout=60, dry_run=dry_run)


STREAM_HEALS = [
    ("restart-lumen-pokemon-live", heal_restart_live),
    ("restart-cloudflared", heal_restart_cloudflared),
]


RETIRED_LOAD_STATES = ("masked", "not-found")
LIVE_AGENT_CHECKS = ("S1-public", "S2-unit", "S4-local")


def unit_load_state(unit):
    """systemd LoadState for `unit`, or None when systemctl cannot say. A
    unit we cannot inspect is never treated as retired."""
    rc, out = systemctl(["show", "-p", "LoadState", "--value", unit])
    return out.strip() if rc == 0 and out.strip() else None


def registry(cfg):
    reg = {
        "S1-public": {"fn": check_s1_public, "interval_s": 0, "heals": STREAM_HEALS},
        "S2-unit": {"fn": check_s2_unit, "interval_s": 0,
                    "heals": [STREAM_HEALS[0]]},
        "S3-tunnel": {"fn": check_s3_tunnel, "interval_s": 0,
                      "heals": [STREAM_HEALS[1]]},
        "S4-local": {"fn": check_s4_local, "interval_s": 0,
                     "heals": [STREAM_HEALS[0]]},
    }
    # A masked or deleted live-agent unit is an operator decision (see
    # docs/fleet/retired_services.yaml), not an outage: S1/S2/S4 would go RED
    # every cycle, run a `systemctl restart` that cannot succeed, then open a
    # fire nobody can close. Re-evaluated every pass, so unmasking re-arms them.
    unit = _cfg(cfg)["unit"]
    load = unit_load_state(unit)
    if load in RETIRED_LOAD_STATES:
        print(f"guardian: {'/'.join(LIVE_AGENT_CHECKS)} not run — {unit} is {load} "
              f"(retired); they re-arm when the unit loads again")
        for check_id in LIVE_AGENT_CHECKS:
            del reg[check_id]
    return reg
