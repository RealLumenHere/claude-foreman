#!/usr/bin/env python3
"""node_power.py — cold/warm power lifecycle (wq#2254, C6 of wq#2241; INFERENCE_NODE_REGISTRY.md §6).

Wake ONE cold node when the local lane is starved and every live member is full; power a cold node
down when it has been idle long enough. Called once per dispatch pass, after admissions, by
foreman_dispatcher._node_power_step. The 20-minute pass cadence is the primary anti-flap damping
(dispatcher/systemd/lumen-foreman-dispatch.timer:10); the counters below are the second layer.

    wake   starve_passes=2 consecutive passes with local_slots_full >= 2 AND every live node's backends at
           0 free slots, on a cold-policy node with measured_ready_s set, wakes_today < 4, and
           now - last_drain_ts > 900. plug-ctl on, poll health to 2 * measured_ready_s.
    drain  idle_passes=2 passes with zero units on the node and zero ready-but-unspawned local tickets,
           and now - last_wake_ts >= 3600. Six steps, in this order and no other (§6.3):
           1 state draining + render   2 confirm zero in-flight   3 systemctl stop <unit>
           4 poweroff                  5 wait for ssh to stop     6 plug-ctl off — strictly LAST.
           The MacPro6,1 auto-powers-on out of G3 (research/ISIS_POWEROFF_RCA.md:70), so cutting AC under
           a running OS is a power-cycle loop wearing the mask of a crash.

Interlock (§6.4), before any plug off: plug_id == `fleet-inv plugs`, ssh reports the expected hostname,
plug watts non-zero and within 30% of idle_watts. It runs BEFORE step 1, while the box is still up: by
step 5 ssh is gone and the plug reads standby watts, so the same checks at step 6 could never pass. Step 6
re-checks the plug binding alone.

Cross-pass counters: <FOREMAN_RUNS_ROOT>/power_state.json
    {node: {starved_passes, idle_passes, last_wake_ts, last_drain_ts, wakes_today, wake_log, excluded_until}}
wakes_today counts the wake_log entries of the last 24 h (rolling, so midnight never hands out a second quota).
Every action goes to lumen_state as <node>.power_action and to the pass log. Kill switch:
FOREMAN_NODE_POWER=off (read per pass) keeps every decision logged and moves no AC, the same shape as
FOREMAN_LOCAL_CAPACITY=static. Thor has no registry row and never gets one, so it can never be touched here.
"""
import json
import os
import time

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(DISPATCHER_DIR)
RUNS_ROOT = os.environ.get("FOREMAN_RUNS_ROOT", os.path.join(REPO_ROOT, "_runs"))
STATE_PATH = os.environ.get("FOREMAN_POWER_STATE", os.path.join(RUNS_ROOT, "power_state.json"))

STARVE_PASSES, STARVE_MIN = 2, 2
IDLE_PASSES = 2
MAX_WAKES_PER_DAY = 4
COOL_S = 900
MIN_WARM_S = 3600
DAY_S = 86400
EXCLUDE_S = DAY_S            # a node whose wake failed sits out a day
HEALTH_POLL_S = 15
SSH_DOWN_DEADLINE_S, SSH_POLL_S = 180, 5
SETTLE_S = 30                # ssh gone is early in the shutdown; let the OS halt before AC moves
WATTS_TOLERANCE = 0.30


class PowerError(Exception):
    pass


def power_mode():
    """'off' when the kill switch is pulled, else 'on'."""
    return "off" if os.environ.get("FOREMAN_NODE_POWER", "").strip() == "off" else "on"


class Signals:
    """One pass's view: `free_slots(backend_id)` is LocalCapacity.free_slots, `running_models()` the model of
    every running local unit (called only when a cold node is up, so a registry with none costs no
    lumen-run call), `ready_pending` the ready local tickets that did not get a slot."""

    def __init__(self, local_slots_full, free_slots, running_models, ready_pending):
        self.local_slots_full, self.free_slots = local_slots_full, free_slots
        self.running_models, self.ready_pending = running_models, ready_pending


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def load_state(path=None):
    try:
        with open(path or STATE_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state, path=None):
    path = path or STATE_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def parse_watts(out):
    """`plug-ctl <p> watts` prints "<p> <state> <watts>"; None when the plug did not answer."""
    toks = out.split()
    if len(toks) < 3 or toks[1] == "unreachable":
        return None
    try:
        return float(toks[-1])
    except ValueError:
        return None


def interlock(ports, node, row):
    """§6.4: the reasons a plug-off on `node` is refused; [] when all three conditions hold."""
    fails = []
    plug = row.get("plug_id")
    try:
        inv_plug = ports.plugs().get(node)
    except OSError as e:
        fails.append(f"fleet-inv plugs unreadable: {e}")
    else:
        if not plug or inv_plug != plug:
            fails.append(f"registry plug_id {plug!r} but fleet-inv plugs has {inv_plug!r} for {node}")
    rc, out = ports.ssh(node, "hostname", timeout=15)
    host = out.strip().split(".")[0].lower() if rc == 0 and out.strip() else None
    if host != node.lower():
        fails.append(f"ssh reports hostname {host!r}, expected {node!r}")
    idle = row.get("idle_watts")
    if not (_num(idle) and idle > 0):
        fails.append("idle_watts is not measured, so the plug reading cannot be checked")
    elif plug:
        rc, out = ports.plug(plug, "watts")
        w = parse_watts(out) if rc == 0 else None
        if not w or abs(w - idle) > WATTS_TOLERANCE * idle:
            fails.append(f"plug {plug} reads {w} W, not within {int(WATTS_TOLERANCE * 100)}% of idle {idle} W")
    return fails


def default_ports():
    from node_ports import SystemPorts
    return SystemPorts()


class NodePower:
    def __init__(self, ports=None, registry_path=None, root=None, state_path=None, emit=None):
        import inference_registry as ir
        self.ir = ir
        self.ports = ports or default_ports()
        self.registry_path = registry_path or ir.REGISTRY_YAML
        self.root = root or os.path.normpath(ir.REPO_ROOT)
        self.state_path = state_path or STATE_PATH
        self._emit = emit or (lambda kind, **kw: None)

    # --- helpers -------------------------------------------------------------------------------
    def emit(self, kind, **fields):
        self._emit(kind, **fields)

    def _action(self, node, row, verb, trigger, **detail):
        """The audit line: <node>.power_action in lumen_state plus the pass log."""
        rec = {"at": int(self.ports.now()), "verb": verb, "plug": row.get("plug_id"), "trigger": trigger, **detail}
        self.emit("power_action", node=node, **rec)
        try:
            self.ports.state_write(node, "power_action", json.dumps(rec, sort_keys=True))
        except Exception as e:  # noqa: BLE001 — the audit mirror never blocks the action it records
            self.emit("power_error", node=node, reason="lumen_state_write_failed", detail=repr(e)[:200])

    def _backends(self, reg, node):
        import node_render as nr
        return [b for b in nr.backends(reg) if b["node"] == node]

    def _health_urls(self, reg, node):
        rows = self._backends(reg, node)
        rows = [b for b in rows if b["model"].get("resident")] or rows
        return sorted({f"http://{b['ip']}:{b['family']['port']}{b['family']['health_path']}" for b in rows})

    def _set_state(self, node, state):
        """Registry `state:` edit plus a render of the in-git targets; the registry is restored if the
        render fails, so the two never disagree. The gateway is not touched: a draining or cold node keeps
        its deployment there (LiteLLM cooldown), only the dispatcher's view has to move."""
        import node_render as nr
        import registry_edit as ed
        with open(self.registry_path) as f:
            old = f.read()
        with open(self.registry_path, "w") as f:
            f.write(ed.set_field(old, node, "state", state))
        try:
            nr.apply(self.ir.load(self.registry_path), self.root)
        except Exception as e:  # noqa: BLE001
            with open(self.registry_path, "w") as f:
                f.write(old)
            raise PowerError(f"render after state={state} failed: {e}") from e

    def _in_flight(self, reg, node, models):
        """Running local units on `node`, plus units that cannot be attributed to any backend (a group name
        or an unreadable model): those may be on this node, so they block a power-down."""
        import local_capacity
        ids = {b["id"] for b in self._backends(reg, node)}
        known = {b["id"] for b in self._all_backends(reg)}
        n = 0
        for m in models:
            base = local_capacity.base_name(m)
            if base in ids or base not in known:
                n += 1
        return n

    def _all_backends(self, reg):
        import node_render as nr
        return nr.backends(reg)

    # --- the pass ------------------------------------------------------------------------------
    def step(self, sig, dry_run=False):
        """One pass's power decision. At most one action (a wake or a power-down) per pass."""
        reg = self.ir.load(self.registry_path)
        nodes = {n: r for n, r in (reg.get("nodes") or {}).items() if isinstance(r, dict)}
        cold = {n: r for n, r in nodes.items() if r.get("power_policy") == "cold"}
        if not cold:
            return None
        now, mode = self.ports.now(), power_mode()
        state = load_state(self.state_path)
        for n in cold:
            e = state.setdefault(n, {})
            # a rolling 24 h window, not a calendar day: a day boundary must not hand out a second quota
            e["wake_log"] = [t for t in e.get("wake_log", []) if now - t < DAY_S]
            e["wakes_today"] = len(e["wake_log"])
            for k in ("starved_passes", "idle_passes"):
                e.setdefault(k, 0)

        live_full = all(sig.free_slots(b["id"]) == 0
                        for n, r in nodes.items() if r.get("state") == "live"
                        for b in self._backends(reg, n))
        starved = sig.local_slots_full >= STARVE_MIN and live_full
        idle_work = sig.ready_pending == 0
        running = None
        for n, r in cold.items():
            e = state[n]
            e["starved_passes"] = e["starved_passes"] + 1 if starved else 0
            if r.get("state") == "live":
                running = sig.running_models() if running is None else running
                units = self._in_flight(reg, n, running)
                e["idle_passes"] = e["idle_passes"] + 1 if units == 0 and idle_work else 0
            else:
                e["idle_passes"] = 0

        action = None
        wake = self._pick_wake(cold, state, now, starved)
        if wake:
            action = self._decide(reg, wake, "wake", state, sig, mode, dry_run)
        else:
            for n, r in sorted(cold.items()):
                if r.get("state") == "live" and state[n]["idle_passes"] >= IDLE_PASSES:
                    action = self._decide(reg, n, "power_down", state, sig, mode, dry_run, now=now)
                    if action:
                        break
        if not dry_run:
            save_state(state, self.state_path)
        return action

    def _pick_wake(self, cold, state, now, starved):
        """The one node to wake, or None. Logs why a starved fleet did not wake (the decision, not the act)."""
        if not starved:
            return None
        cands = []
        for n, r in cold.items():
            e = state[n]
            if e["starved_passes"] < STARVE_PASSES:
                continue
            why = []
            if r.get("state") == "down":
                if "excluded_until" not in e:
                    why.append("state down (not a failed wake)")
            elif r.get("state") != "cold":
                continue
            ready = (r.get("wake") or {}).get("measured_ready_s")
            if not (_num(ready) and ready > 0):
                why.append("measured_ready_s unset")
            if e["wakes_today"] >= MAX_WAKES_PER_DAY:
                why.append(f"wakes_today {e['wakes_today']} >= {MAX_WAKES_PER_DAY}")
            if now - e.get("last_drain_ts", 0) <= COOL_S:
                why.append(f"drained {int(now - e['last_drain_ts'])}s ago, cool_s {COOL_S}")
            if now < e.get("excluded_until", 0):
                why.append(f"excluded until {int(e['excluded_until'])}")
            if why:
                self.emit("wake_blocked", node=n, reasons=why)
            else:
                cands.append(n)
        if not cands:
            return None

        def rank(n):
            models = cold[n].get("models") or []
            tps = max([m["prefill_tps"] for m in models if _num(m.get("prefill_tps"))] or [0])
            idle = cold[n].get("idle_watts")
            return (-tps, idle if _num(idle) else float("inf"), n)
        return min(cands, key=rank)

    def _decide(self, reg, node, verb, state, sig, mode, dry_run, now=None):
        counters = {k: state[node].get(k) for k in ("starved_passes", "idle_passes", "wakes_today")}
        counters["local_slots_full"] = sig.local_slots_full
        if mode == "off" or dry_run:
            self.emit("power_decision", node=node, verb=verb, acted=False,
                      blocked="kill_switch FOREMAN_NODE_POWER=off" if mode == "off" else "dry_run", **counters)
            return None
        if verb == "wake":
            return self.wake(reg, node, state, counters)
        e = state[node]
        if now - e.get("last_wake_ts", 0) < MIN_WARM_S:
            self.emit("power_decision", node=node, verb=verb, acted=False,
                      blocked=f"inside min_warm_s {MIN_WARM_S}", **counters)
            return None
        return self.power_down(node, "idle", state=state, counters=counters, reg=reg)

    # --- wake ----------------------------------------------------------------------------------
    def wake(self, reg, node, state, counters):
        row, e, ports = reg["nodes"][node], state[node], self.ports
        # counted before the AC moves: a crash mid-wake still spends the quota
        e["last_wake_ts"], e["starved_passes"] = ports.now(), 0
        e["wake_log"].append(e["last_wake_ts"])
        e["wakes_today"] = len(e["wake_log"])
        save_state(state, self.state_path)
        self._action(node, row, "on", "starvation", **counters)
        rc, _ = ports.plug(row["plug_id"], "on")
        if rc != 0:
            e["excluded_until"] = ports.now() + EXCLUDE_S
            self.emit("power_error", node=node, reason="plug_on_failed", rc=rc)
            return {"node": node, "verb": "wake", "ok": False, "why": "plug_on_failed"}
        urls, deadline = self._health_urls(reg, node), row["wake"]["measured_ready_s"] * 2
        t0 = ports.clock()
        while not all(ports.probe(u) for u in urls):
            if ports.clock() - t0 >= deadline:
                return self._wake_failed(reg, node, state, deadline)
            ports.sleep(HEALTH_POLL_S)
        e.pop("excluded_until", None)
        try:
            self._set_state(node, "live")
        except PowerError as err:
            self.emit("power_error", node=node, reason="render_failed", detail=str(err)[:200])
        self.emit("wake_ok", node=node, ready_s=round(ports.clock() - t0))
        return {"node": node, "verb": "wake", "ok": True}

    def _wake_failed(self, reg, node, state, deadline):
        """A wake that half-works is worse than one that never started: mark it down, exclude it for a
        day and power it back off through the same interlocked sequence as any other power-down."""
        e = state[node]
        e["excluded_until"] = self.ports.now() + EXCLUDE_S
        self.emit("power_error", node=node, reason="wake_health_timeout", deadline_s=deadline)
        try:
            self._set_state(node, "down")
        except PowerError as err:
            self.emit("power_error", node=node, reason="render_failed", detail=str(err)[:200])
        self.power_down(node, "wake_failed", state=state, reg=reg, manage_state=False)
        return {"node": node, "verb": "wake", "ok": False, "why": "wake_health_timeout"}

    # --- power down ----------------------------------------------------------------------------
    def power_down(self, node, trigger, state=None, counters=None, reg=None, manage_state=True):
        """The §6.3 sequence. `manage_state=False` leaves the registry `state` alone (fleet-node retire owns
        it there). Returns {"node", "verb", "ok"}; a refusal or abort leaves the plug ON."""
        ports = self.ports
        reg = reg or self.ir.load(self.registry_path)
        row = reg["nodes"][node]
        e = state.setdefault(node, {}) if state is not None else {}
        result = {"node": node, "verb": "off", "ok": False}
        fails = interlock(ports, node, row)
        if fails:
            self.emit("power_refused", node=node, trigger=trigger, reasons=fails)
            self._action(node, row, "off_refused", trigger, reasons=fails)
            return result
        prior = row.get("state", "live")
        try:
            if manage_state:
                self._set_state(node, "draining")                            # 1
            models = ports.running_local_models()                            # 2
            if self._in_flight(reg, node, models):
                raise PowerError("units still in flight; re-armed")
            for unit in self._units(reg, node):                              # 3
                rc, _ = ports.ssh(node, f"sudo -n systemctl stop {unit}.service", timeout=120)
                if rc != 0:
                    raise PowerError(f"systemctl stop {unit} exited {rc}")
            ports.ssh(node, "sudo -n systemctl poweroff", timeout=30)        # 4: the link may drop
            t0 = ports.clock()                                               # 5
            while ports.ssh(node, "true", timeout=10)[0] == 0:
                if ports.clock() - t0 >= SSH_DOWN_DEADLINE_S:
                    self._stuck(node, row, e, manage_state, "ssh still answering after poweroff")
                    return result
                ports.sleep(SSH_POLL_S)
            ports.sleep(SETTLE_S)
            if ports.plugs().get(node) != row.get("plug_id"):                # 6 — binding re-read, then AC
                raise PowerError("plug binding changed during the drain")
            rc, _ = ports.plug(row["plug_id"], "off")
            if rc != 0:
                self._stuck(node, row, e, manage_state, f"plug-ctl off exited {rc}")
                return result
        except (PowerError, OSError) as err:
            return self._abort(node, row, e, prior, manage_state, str(err), result)
        e["last_drain_ts"], e["idle_passes"] = ports.now(), 0
        self._action(node, row, "off", trigger, **(counters or {}))
        if manage_state:
            try:
                self._set_state(node, "cold")
            except PowerError as err:
                self.emit("power_error", node=node, reason="render_failed", detail=str(err)[:200])
        result["ok"] = True
        return result

    def _units(self, reg, node):
        from node_ports import unit_name
        fams = reg.get("families") or {}
        units = []
        for m in reg["nodes"][node].get("models") or []:
            fam = fams.get(m.get("family")) or {}
            u = "ollama" if fam.get("serving") == "ollama" else unit_name(m.get("family"), fam)
            if u not in units:
                units.append(u)
        return units

    def _abort(self, node, row, e, prior, manage_state, why, result):
        """Before any AC moved: put the node back as it was and re-arm the idle counter."""
        self.emit("power_error", node=node, reason="power_down_aborted", detail=why[:200])
        e["idle_passes"] = 0
        if manage_state:
            try:
                self._set_state(node, prior)
            except PowerError as err:
                self.emit("power_error", node=node, reason="render_failed", detail=str(err)[:200])
        return result

    def _stuck(self, node, row, e, manage_state, why):
        """The OS was told to power off and the plug was NOT cut: never cut AC under a running OS. The node
        is marked down and excluded from wake so a human looks at it."""
        self.emit("power_error", node=node, reason="power_down_stuck", detail=why)
        e["excluded_until"] = self.ports.now() + EXCLUDE_S
        if manage_state:
            try:
                self._set_state(node, "down")
            except PowerError as err:
                self.emit("power_error", node=node, reason="render_failed", detail=str(err)[:200])


def run(sig, dry_run=False, emit=None, ports=None, registry_path=None, root=None, state_path=None):
    """The dispatcher's entry point: one pass. Returns the action taken or None."""
    return NodePower(ports=ports, registry_path=registry_path, root=root, state_path=state_path,
                     emit=emit).step(sig, dry_run=dry_run)
