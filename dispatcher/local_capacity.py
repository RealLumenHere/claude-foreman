#!/usr/bin/env python3
"""local_capacity.py — per-backend local slots + the local-pool pause (wq#2130).

DISPATCH_REBUILD_SPEC.md §2.1 / §3 step 6. The local rung used to be sized by one integer
(`local_ceiling`) counted over every local unit: three Macs with different real capacity
looked like one pool, and a dead box still "had room". This module sizes each backend on
its own from config.json `local_backends`:

    "hathor-llama8b": {"health_url": ..., "slots": 2, "served_ctx": 32768, "first_frame_s": 1500}
    "local-worker":   {"group": [<backend names>]}

`free_slots(model)` = slots - running units pinned/routed to that model, and 0 when the
backend's health probe fails (2 s, once per pass, cached) or the backend is listed in
runtime/backend_cooldown.json. A group entry (the LiteLLM `local-worker` group) is the sum
over its healthy members, less the units running on the group name itself.

The group name is a ROUTING name (wq#2154): `pick_member(group)` resolves it to a concrete
member at admit time and the spawn carries the member as the worker's model, so neither the
governor CLI nor fworker ever sees the group (they only know the per-host names).

Also owns runtime/local_pause.json, the file circuit_breaker.py writes when the LOCAL pool
trips and the dispatcher's local partition reads. It is a separate file from
DISPATCH_DISARM on purpose: a local trip must never stop the cloud partition.

Rollback lever: FOREMAN_LOCAL_CAPACITY=static (read by the dispatcher) skips all of this
and falls back to the old `local_ceiling` count.
"""
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.environ.get("FOREMAN_RUNTIME_DIR", os.path.join(DISPATCHER_DIR, "runtime"))
COOLDOWN_PATH = os.environ.get("FOREMAN_BACKEND_COOLDOWN",
                               os.path.join(RUNTIME_DIR, "backend_cooldown.json"))
LOCAL_PAUSE_PATH = os.environ.get("FOREMAN_LOCAL_PAUSE",
                                  os.path.join(RUNTIME_DIR, "local_pause.json"))
PROBE_TIMEOUT_S = 2.0
LOCAL_PAUSE_MINUTES = int(os.environ.get("FOREMAN_LOCAL_PAUSE_MIN", "60"))


def capacity_mode():
    """'static' when the rollback lever is pulled, else 'live'."""
    return "static" if os.environ.get("FOREMAN_LOCAL_CAPACITY", "").strip() == "static" \
        else "live"


def base_name(model):
    """`hathor-llama8b@low` -> `hathor-llama8b`; None stays None."""
    return model.split("@", 1)[0] if model else model


def first_member(backends, group):
    """First configured member of `group` with a row, or None. The static-mode resolver
    (FOREMAN_LOCAL_CAPACITY=static has no health view): the group name still must not leave
    the dispatcher."""
    g = (backends or {}).get(base_name(group)) if isinstance(backends, dict) else None
    members = g.get("group") if isinstance(g, dict) else None
    for m in members if isinstance(members, list) else []:
        if isinstance(m, str) and isinstance((backends or {}).get(m), dict):
            return m
    return None


def http_probe(url, timeout=PROBE_TIMEOUT_S):
    """True iff GET `url` answers 2xx within `timeout`. Any failure is 'down'."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:  # noqa: BLE001 — a probe that cannot answer is a backend that is down
        return False


def _epoch(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    return None


def cooled_down_backends(now=None, path=None):
    """Backends whose cooldown has not expired, from runtime/backend_cooldown.json:
    {"<backend>": <until epoch|ISO>} or {"<backend>": {"until": <epoch|ISO>, ...}}.
    Unreadable or malformed = none (the health probe is still the primary gate)."""
    now = now if now is not None else time.time()
    try:
        with open(path or COOLDOWN_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    out = set()
    for name, v in data.items():
        until = _epoch(v.get("until") if isinstance(v, dict) else v)
        if until is not None and until > now:
            out.add(base_name(name))
    return out


def read_pause(now=None, path=None):
    """Active local-pool pause as a dict {'until', 'reason', ...}, else None. A missing,
    expired or unreadable file is no pause: an unreadable pause must not wedge the rung."""
    now = now if now is not None else time.time()
    try:
        with open(path or LOCAL_PAUSE_PATH) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    until = _epoch(rec.get("until")) if isinstance(rec, dict) else None
    if until is None or until <= now:
        return None
    return rec


def write_pause(reason, stats=None, now=None, minutes=None, path=None):
    """Write the local-pool pause (atomic). Never touches DISPATCH_DISARM."""
    now = now if now is not None else time.time()
    minutes = minutes if minutes is not None else LOCAL_PAUSE_MINUTES
    path = path or LOCAL_PAUSE_PATH
    rec = {"tripped_at": now, "until": now + minutes * 60, "minutes": minutes,
           "reason": reason, "stats": stats or {}}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2)
    os.replace(tmp, path)
    return rec


class LocalCapacity:
    """One pass's view of the local backends. `backends` is config.json local_backends;
    `running` returns the model of every running local unit (re-called on each query so a
    spawn earlier in the pass is seen); `probe` is injectable for tests."""

    def __init__(self, backends, running, probe=None, cooldown_path=None, now=None):
        self.backends = backends if isinstance(backends, dict) else {}
        self._running = running
        self._probe = probe if probe is not None else (lambda url: http_probe(url))
        self._cooldown_path = cooldown_path
        self._now = now
        self._health = None       # url -> bool, filled once per pass
        self._cooling = None

    def knows(self, model):
        return isinstance(self.backends.get(base_name(model)), dict)

    def is_group(self, model):
        return self._members(base_name(model)) is not None

    def _entry(self, name):
        e = self.backends.get(name)
        return e if isinstance(e, dict) else {}

    def _members(self, name):
        g = self._entry(name).get("group")
        return [m for m in g if isinstance(m, str)] if isinstance(g, list) else None

    def _probe_all(self):
        urls = sorted({e["health_url"] for e in self.backends.values()
                       if isinstance(e, dict) and isinstance(e.get("health_url"), str)})
        if not urls:
            self._health = {}
            return
        with ThreadPoolExecutor(max_workers=len(urls)) as ex:  # <= one probe timeout total
            self._health = dict(zip(urls, ex.map(self._probe, urls)))

    def healthy(self, name):
        """Probe answered (cached for the pass) and the backend is not cooling down."""
        if self._health is None:
            self._probe_all()
        if self._cooling is None:
            self._cooling = cooled_down_backends(self._now, self._cooldown_path)
        url = self._entry(name).get("health_url")
        return bool(url) and self._health.get(url, False) and name not in self._cooling

    def _running_counts(self):
        counts = {}
        for m in self._running():
            counts[base_name(m)] = counts.get(base_name(m), 0) + 1
        return counts

    def free_slots(self, model):
        """Free slots for `model` (a backend or the group name). 0 when unknown to
        local_backends, down, or cooling down — callers that want a fallback for an
        unconfigured model check knows() first."""
        name = base_name(model)
        if not self.knows(name):
            return 0
        running = self._running_counts()
        members = self._members(name)
        if members is not None:
            live = [m for m in members if self.knows(m) and self.healthy(m)]
            total = sum(self._slots(m) for m in live)
            used = sum(running.get(m, 0) for m in live) + running.get(name, 0)
            return max(0, total - used)
        if not self.healthy(name):
            return 0
        return max(0, self._slots(name) - running.get(name, 0))

    def live_slots(self):
        """Total slots of the concrete backends that answer their probe and are not cooling
        down (group rows carry none of their own) — capacity ignoring what is running."""
        return sum(self._slots(n) for n, e in self.backends.items()
                   if isinstance(e, dict) and "health_url" in e and self.healthy(n))

    def _slots(self, name):
        s = self._entry(name).get("slots")
        return s if isinstance(s, int) and not isinstance(s, bool) and s > 0 else 0

    def pick_member(self, group):
        """The backend a `group` ticket runs on (wq#2154): the healthy, not-cooling member
        with the most free slots, ties in config order. None when `group` is not a group or
        no member has a free slot. The group name is a routing name only — it must never
        reach the governor, fworker or a unit's --model, so the caller spawns on this."""
        name = base_name(group)
        members = self._members(name)
        if members is None or self.free_slots(name) <= 0:
            return None
        running = self._running_counts()
        best, best_free = None, 0
        for m in members:
            if not (self.knows(m) and self.healthy(m)):
                continue
            free = self._slots(m) - running.get(m, 0)
            if free > best_free:
                best, best_free = m, free
        return best

    def spawn_env(self, model):
        """{FOREMAN_FIRST_FRAME_S, FOREMAN_SERVED_CTX, FOREMAN_PREFILL_TPS} for a spawn on
        `model` (each only when the row carries it); {} when the
        model has no local_backends row. Callers pass the resolved member, never the group
        (a group row carries no budget of its own)."""
        name = base_name(model)
        if not self.knows(name) or self._members(name) is not None:
            return {}
        e = self._entry(name)
        env = {}
        if isinstance(e.get("first_frame_s"), int):
            env["FOREMAN_FIRST_FRAME_S"] = str(e["first_frame_s"])
        if isinstance(e.get("served_ctx"), int):
            env["FOREMAN_SERVED_CTX"] = str(e["served_ctx"])
        tps = e.get("prefill_tps")
        if isinstance(tps, (int, float)) and not isinstance(tps, bool) and tps > 0:
            env["FOREMAN_PREFILL_TPS"] = str(tps)
        return env

    def snapshot(self):
        """{backend: {healthy, slots, running}} for the pass log / dashboard."""
        running = self._running_counts()
        return {n: {"healthy": self.healthy(n) if "health_url" in e else None,
                    "slots": self._slots(n), "running": running.get(n, 0),
                    "free": self.free_slots(n)}
                for n, e in self.backends.items() if isinstance(e, dict)}
