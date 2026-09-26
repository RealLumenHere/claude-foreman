#!/usr/bin/env python3
"""split_watch — edge-triggered idle/error alerting for the agent5 split
(planner/generator/evaluator on Redis db5). wq#140.

wq#1643: the three units are confirmed LoadState=not-found (structurally
retired, not masked/transient) -- see docs/fleet/retired_services.yaml. This
guard now self-detects that and drains+goes permanently inert instead of
polling a ghost pipeline and printing a misleading "ok" forever.

Signals:
  STALL  — any work queue (plan_in/gen_in/eval_in) has items AND its depth has
           not decreased for >30 min (tracked across runs in state.json).
  DEAD   — lumen5:dead grew since last run (per-item terminal failures).
  UNIT   — any of the 3 systemd units not active.

Edge-triggered: alerts fire on transition into a bad state, one RESOLVED notice
on recovery. State: /home/lumen/agents/split-watch/state.json. Alerts -> local
ntfy topic lumen-alerts (picked up by aq via lumen-alert-queue.service).
Run by lumen-split-watch.timer every 10 min. Fail-open on redis/ntfy errors.
"""
import json, pathlib, subprocess, time

import redis, requests

STATE = pathlib.Path("/home/lumen/agents/split-watch/state.json")
QUEUES = ["lumen5:plan_in", "lumen5:gen_in", "lumen5:eval_in"]
UNITS = ["lumen-planner", "lumen-generator", "lumen-evaluator"]
STALL_SEC = 30 * 60
NTFY = "http://localhost:8080/lumen-alerts"

def ntfy(title, msg, prio="high"):
    try:
        requests.post(NTFY, data=msg.encode(),
                      headers={"Title": title, "Priority": prio}, timeout=5)
    except Exception as e:
        print(f"[ntfy fail-open] {e}")

RETIRED_DOC = "docs/fleet/retired_services.yaml"

def _load_state(unit: str) -> str:
    """systemd LoadState for `unit` ('unknown' on any lookup failure, wq#2713 --
    a failed lookup must never trigger the destructive drain+retire below)."""
    # wq#2713: a lookup FAILURE is not evidence of retirement. The old body
    # returned 'not-found' on timeout/exception/empty stdout, so a transient
    # systemctl hiccup filed "retired unit, delete the row" tickets against LIVE
    # units (redis-server, active since 08-31) -- and a worker built a PR to do it.
    # Only a successful systemctl answer may say not-found/masked.
    try:
        r = subprocess.run(["systemctl", "show", unit, "-p", "LoadState"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return "unknown"
    if r.returncode != 0:
        return "unknown"
    return dict(l.split("=", 1) for l in r.stdout.strip().splitlines()
                if "=" in l).get("LoadState") or "unknown"

def load():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}

def main():
    st = load()
    now = int(time.time())

    if st.get("retired"):
        print(f"agent5 split RETIRED {st.get('retired_at')} — guard inert, "
              f"see {RETIRED_DOC}")
        return

    if all(_load_state(u) == "not-found" for u in UNITS):
        # Structural retirement, not a transient outage (wq#1643): drain the
        # stuck queues once and go permanently quiet instead of restarting
        # the same edge-triggered alert forever over a pipeline that cannot
        # come back.
        r = redis.Redis(db=5)
        drained = {q: r.llen(q) for q in QUEUES + ["lumen5:dead"]}
        for q in QUEUES + ["lumen5:dead"]:
            r.delete(q)
        st = {"retired": True, "retired_at": now, "drained": drained}
        STATE.write_text(json.dumps(st))
        msg = (f"agent5 split (planner/generator/evaluator) confirmed "
               f"RETIRED -- drained {drained}. Guard now inert, see {RETIRED_DOC}")
        print(msg)
        ntfy("Homelab: agent5 split RETIRED", msg, "default")
        return

    r = redis.Redis(db=5)
    alerts, resolves = [], []

    # ── queue stalls ──
    for q in QUEUES:
        depth = r.llen(q)
        prev = st.get(q, {})
        if depth == 0 or depth < prev.get("depth", 0):
            # empty or draining = healthy
            if prev.get("alerted"):
                resolves.append(f"{q} recovered (depth={depth})")
            st[q] = {"depth": depth, "since": now, "alerted": False}
            continue
        since = prev.get("since", now)
        if depth > prev.get("depth", -1):
            since = prev.get("since", now)  # growth keeps the clock (still not draining)
        stalled = (now - since) > STALL_SEC
        alerted = prev.get("alerted", False)
        if stalled and not alerted:
            alerts.append(f"{q} STALLED: depth={depth}, no drain for "
                          f"{(now - since)//60} min")
            alerted = True
        st[q] = {"depth": depth, "since": since, "alerted": alerted}

    # ── dead-queue growth ──
    dead = r.llen("lumen5:dead")
    prev_dead = st.get("dead_count", dead)
    if dead > prev_dead:
        alerts.append(f"lumen5:dead grew {prev_dead} -> {dead} "
                      f"(+{dead - prev_dead} terminal failures)")
    st["dead_count"] = dead

    # ── unit liveness ──
    for u in UNITS:
        active = subprocess.run(["systemctl", "is-active", "--quiet", u]).returncode == 0
        was_down = st.get(f"unit:{u}", False)
        if not active and not was_down:
            alerts.append(f"{u} NOT ACTIVE")
        elif active and was_down:
            resolves.append(f"{u} active again")
        st[f"unit:{u}"] = not active

    STATE.write_text(json.dumps(st))
    for a in alerts:
        print("ALERT:", a)
        ntfy("Homelab: agent5 split", a, "high")
    for m in resolves:
        print("RESOLVED:", m)
        ntfy("Homelab: agent5 split", f"RESOLVED: {m}", "default")
    if not alerts and not resolves:
        print(f"ok depths={[r.llen(q) for q in QUEUES]} dead={dead}")

if __name__ == "__main__":
    main()
