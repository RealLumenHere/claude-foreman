#!/usr/bin/env python3
"""model_swap.py — per-task model choice and unit-swap admission (wq#2255, C7 of wq#2241;
INFERENCE_NODE_REGISTRY.md §7.1 and §7.3). Acceptance is the routing and swap-refusal part of T9 (§8).

route()   Per archetype the candidates are every live registry (node, family) whose capability row is
          trusted and whose served_ctx clears prompt_tokens_est, ranked by measured success, then
          prefill_tps (a row with no measured success sorts after every measured one). The winner is named
          <family>.<node> (§4) and carries `backend`, the id local_capacity accounts slots under. The
          first candidate that can serve NOW wins: a resident model, or an `implicit` (ollama) family,
          which lazy-loads inside the request and never touches a unit. A `unit` (llama-server) family
          that is not resident is skipped; only step() may swap.
step()    Once per dispatch pass. For each node with a non-resident `unit` family that ready tickets
          want, checks the admission conditions and, when all hold, stops the resident unit and starts
          the target's. A unit stop destroys the KV cache of every slot on the box (ollama 0.32.6 forces
          one slot for the qwen3.6 MoE arch, research/QWEN36_LOCAL_WORKER_SERVING.md:24-26), so:
              in_flight   zero in-flight slots on the node ...
              settle      ... and for swap_settle_s (120), sampled at pass cadence: a node is idle
                          only once a pass has seen it idle and swap_settle_s has passed since
              queue       ready tickets wanting the target family on this node >= swap_min_queue (3)
              resident    zero ready tickets wanting the resident family on this node
              cool        now - last_swap_ts >= swap_cool_s (1800)
              cap         fewer than max_swaps_per_day (6) swaps in the last 24 h (rolling)
              load_s      the target row's load_s is measured; null means no swap is ever attempted
          Every failed condition is logged by name, on one `swap_refused` line. A ticket "wants" the top
          candidate of its archetype ranked with swap cost ignored.
          Spreading families across nodes is the steady state; a swap is for a fleet one box short.

Cross-pass state: <FOREMAN_RUNS_ROOT>/swap_state.json  {node: {idle_since, last_swap_ts, swap_log}}.
Kill switch: FOREMAN_MODEL_SWAP=off (read per pass) keeps every decision logged and moves no unit. A failed
swap restarts the resident unit and still counts toward cool and cap, so a bad target cannot loop.
"""
import json
import os
import sys
import time

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "harness"))
RUNS_ROOT = os.environ.get("FOREMAN_RUNS_ROOT", os.path.join(REPO_ROOT, "_runs"))
STATE_PATH = os.environ.get("FOREMAN_SWAP_STATE", os.path.join(RUNS_ROOT, "swap_state.json"))

SWAP_SETTLE_S = 120
SWAP_MIN_QUEUE = 3
SWAP_COOL_S = 1800
MAX_SWAPS_PER_DAY = 6
DAY_S = 86400
HEALTH_POLL_S = 15
HEALTH_DEADLINE_X = 2         # poll the target's health to this many load_s, then give up


class SwapError(Exception):
    pass


def swap_mode():
    """'off' when the kill switch is pulled, else 'on'."""
    return "off" if os.environ.get("FOREMAN_MODEL_SWAP", "").strip() == "off" else "on"


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


class Signals:
    """One pass's view: `in_flight(node)` is the count of running local units on that node, `ready` the
    ready local tickets as dicts with `archetype` and, when known, `prompt_tokens_est`."""

    def __init__(self, in_flight, ready):
        self.in_flight, self.ready = in_flight, ready


def in_flight_by_node(reg, running_models):
    """{node: running units} from the model of every running local unit. A unit whose model is a group
    name or unknown cannot be placed on a node and is counted on every node with a swappable model: a
    swap under a unit that may be on the box is a swap under load."""
    import local_capacity
    import node_render as nr
    rows = nr.backends(reg)
    by_id = {b["id"]: b["node"] for b in rows}
    counts = {b["node"]: 0 for b in rows}
    for m in running_models:
        node = by_id.get(local_capacity.base_name(m))
        for n in [node] if node else counts:
            counts[n] += 1
    return counts


def candidate_name(family, node):
    return f"{family}.{node}"


def candidates(reg, archetype, prompt_tokens_est=None):
    """Every live (node, family) that may take `archetype`, best first. Cold, down, draining and retired
    nodes are not candidates: node_power wakes a cold one on starvation, the rest are out of service.
    A row with no measured served_ctx cannot prove it holds the prompt, so it is skipped once there is
    a prompt estimate to hold."""
    import node_render as nr
    out = []
    for b in nr.backends(reg):
        if b["state"] != "live":
            continue
        m, fam = b["model"], b["family"]
        cap = (m.get("capabilities") or {}).get(archetype)
        if not (isinstance(cap, dict) and cap.get("trusted") is True):
            continue
        ctx = m.get("served_ctx")
        if _num(prompt_tokens_est) and not (_num(ctx) and ctx >= prompt_tokens_est):
            continue
        success = cap.get("success")
        out.append({"model": candidate_name(m["family"], b["node"]), "backend": b["id"], "node": b["node"],
                    "family": m["family"], "swap": fam.get("swap", "implicit"),
                    "resident": bool(m.get("resident")), "row": m,
                    "success": success if _num(success) else None,
                    "prefill_tps": m.get("prefill_tps") if _num(m.get("prefill_tps")) else None})
    out.sort(key=lambda c: (c["success"] is None, -(c["success"] or 0), c["prefill_tps"] is None,
                            -(c["prefill_tps"] or 0), c["node"], c["family"]))
    return out


def _ticket_candidates(reg, ticket):
    return candidates(reg, ticket.get("archetype"), ticket.get("prompt_tokens_est"))


def wanted(reg, ready):
    """{(node, family): ready tickets whose best candidate, swap cost ignored, is that pair}."""
    counts = {}
    for t in ready:
        ranked = _ticket_candidates(reg, t)
        if ranked:
            key = (ranked[0]["node"], ranked[0]["family"])
            counts[key] = counts.get(key, 0) + 1
    return counts


def route(reg, ticket, usable=None):
    """The model a ticket should run on right now, or None when nothing can serve it without a unit swap.
    Returns the winning candidate dict (see candidates()) plus `skipped`: the better-ranked candidates
    passed over because they need a unit swap. `usable(candidate)` lets the caller veto one it cannot
    place (no free slot); a vetoed candidate is not a swap need and is not listed."""
    skipped = []
    for c in _ticket_candidates(reg, ticket):
        if c["swap"] == "unit" and not c["resident"]:
            skipped.append(c["model"])
            continue
        if usable is not None and not usable(c):
            continue
        return {**c, "skipped": skipped}
    return None


def swaps_today(entry, now):
    return [t for t in entry.get("swap_log", []) if _num(t) and now - t < DAY_S]


class ModelSwap:
    def __init__(self, ports=None, registry_path=None, state_path=None, emit=None):
        import inference_registry as ir
        self.ir = ir
        self.ports = ports
        self.registry_path = registry_path or ir.REGISTRY_YAML
        self.state_path = state_path or STATE_PATH
        self._emit = emit or (lambda kind, **kw: None)

    def emit(self, kind, **fields):
        self._emit(kind, **fields)

    # --- admission -----------------------------------------------------------------------------
    def refusals(self, node, resident, target, in_flight, want, state, now):
        """[(condition, detail)] for every §7.3 condition that fails; [] admits the swap."""
        fails = []
        e = state.get(node) or {}
        if in_flight > 0:
            fails.append(("in_flight", f"{in_flight} slot(s) in flight on {node}"))
        idle = e.get("idle_since")
        if in_flight == 0 and not (_num(idle) and now - idle >= SWAP_SETTLE_S):
            seen = "not yet seen idle" if not _num(idle) else f"idle for {int(now - idle)}s"
            fails.append(("settle", f"{node} {seen}, needs {SWAP_SETTLE_S}s"))
        wt = want.get((node, target["family"]), 0)
        if wt < SWAP_MIN_QUEUE:
            fails.append(("queue", f"{wt} ready ticket(s) want {target['family']}, needs {SWAP_MIN_QUEUE}"))
        wr = want.get((node, resident["family"]), 0)
        if wr:
            fails.append(("resident", f"{wr} ready ticket(s) still want resident {resident['family']}"))
        last = e.get("last_swap_ts")
        if _num(last) and now - last < SWAP_COOL_S:
            fails.append(("cool", f"last swap {int(now - last)}s ago, needs {SWAP_COOL_S}s"))
        n = len(swaps_today(e, now))
        if n >= MAX_SWAPS_PER_DAY:
            fails.append(("cap", f"{n} swaps in the last 24h, max {MAX_SWAPS_PER_DAY}"))
        if not _num(target["row"].get("load_s")):
            fails.append(("load_s", f"{target['model']} has no measured load_s, so no swap is attempted"))
        return fails

    def _swappable(self, reg):
        """{node: (resident candidate-shaped row, [non-resident unit-mode rows])}."""
        import node_render as nr
        per = {}
        for b in nr.backends(reg):
            if b["state"] != "live" or b["family"].get("swap") != "unit":
                continue
            row = {"family": b["model"]["family"], "node": b["node"], "row": b["model"],
                   "model": candidate_name(b["model"]["family"], b["node"]), "backend": b["id"],
                   "fam": b["family"], "ip": b["ip"]}
            slot = per.setdefault(b["node"], [None, []])
            if b["model"].get("resident"):
                slot[0] = row
            else:
                slot[1].append(row)
        return {n: (res, tg) for n, (res, tg) in per.items() if res and tg}

    def observe(self, node, in_flight, state, now):
        e = state.setdefault(node, {})
        if in_flight > 0:
            e["idle_since"] = None
        elif not _num(e.get("idle_since")):
            e["idle_since"] = now

    # --- the pass ------------------------------------------------------------------------------
    def step(self, sig, dry_run=False):
        """One pass. Returns the swap performed (or, under dry_run / the kill switch, the one that would
        have been), else None. Every refusal is emitted with the conditions that failed."""
        reg = self.ir.load(self.registry_path)
        swappable = self._swappable(reg)
        if not swappable:
            return None
        now = self.ports.now() if self.ports else time.time()
        state = load_state(self.state_path)
        want = wanted(reg, sig.ready)
        mode, result = swap_mode(), None
        for node, (resident, targets) in sorted(swappable.items()):
            in_flight = sig.in_flight(node)
            self.observe(node, in_flight, state, now)
            for target in sorted(targets, key=lambda t: -want.get((node, t["family"]), 0)):
                if not want.get((node, target["family"])):
                    continue
                fails = self.refusals(node, resident, target, in_flight, want, state, now)
                info = {"node": node, "from": resident["model"], "to": target["model"],
                        "want": want.get((node, target["family"]), 0),
                        "swap_cost_s": self._cost(resident, target)}
                if fails:
                    self.emit("swap_refused", failed=[c for c, _ in fails],
                              detail="; ".join(d for _, d in fails), **info)
                    continue
                self.emit("swap_admitted", dry_run=dry_run, mode=mode, **info)
                if result is None:                       # one swap per pass
                    result = {**info, "ok": None}
                    if not dry_run and mode == "on":
                        result["ok"] = self.perform(reg, node, resident, target, state, now)
        if not dry_run:
            save_state(state, self.state_path)
        return result

    @staticmethod
    def _cost(resident, target):
        load = target["row"].get("load_s")
        stop = resident["row"].get("unit_stop_s")
        return load + (stop if _num(stop) else 0) if _num(load) else None

    # --- the swap ------------------------------------------------------------------------------
    def _unit(self, side):
        from node_ports import unit_name
        return unit_name(side["family"], side["fam"])

    def _flip(self, node, old_family, new_family):
        import registry_edit as ed
        with open(self.registry_path) as f:
            text = f.read()
        text = ed.set_field(text, node, "resident", False, family=old_family)
        text = ed.set_field(text, node, "resident", True, family=new_family)
        with open(self.registry_path, "w") as f:
            f.write(text)

    def _ssh(self, node, verb, side):
        rc, _ = self.ports.ssh(node, f"sudo -n systemctl {verb} {self._unit(side)}.service", timeout=180)
        if rc != 0:
            raise SwapError(f"systemctl {verb} {self._unit(side)} on {node} exited {rc}")

    def _wait_healthy(self, side, load_s):
        url = f"http://{side['ip']}:{side['fam']['port']}{side['fam']['health_path']}"
        t0 = self.ports.clock()
        while self.ports.clock() - t0 <= HEALTH_DEADLINE_X * load_s:
            if self.ports.probe(url):
                return True
            self.ports.sleep(HEALTH_POLL_S)
        return False

    def perform(self, reg, node, resident, target, state, now):
        """Stop the resident unit, start the target's, wait for health, flip `resident`. Any failure
        restarts the resident unit. Cool and cap count the attempt either way."""
        e = state.setdefault(node, {})
        e["last_swap_ts"] = now
        e["swap_log"] = swaps_today(e, now) + [now]
        e["idle_since"] = None
        ok, why = False, None
        try:
            self._ssh(node, "stop", resident)
            self._ssh(node, "start", target)
            if not self._wait_healthy(target, target["row"]["load_s"]):
                raise SwapError(f"{target['model']} not healthy within "
                                f"{HEALTH_DEADLINE_X * target['row']['load_s']}s")
            self._flip(node, resident["family"], target["family"])
            ok = True
        except SwapError as err:
            why = str(err)
            try:
                self._ssh(node, "stop", target)
                self._ssh(node, "start", resident)
            except SwapError as err2:
                why += f"; restoring {resident['model']} failed: {err2}"
        self.emit("swap_done" if ok else "swap_failed", node=node, to=target["model"],
                  **({} if ok else {"reason": why[:300]}))
        return ok


def run(sig, dry_run=False, emit=None, ports=None, registry_path=None, state_path=None):
    """The dispatcher's entry point: one pass."""
    if ports is None:
        from node_ports import SystemPorts
        ports = SystemPorts()
    return ModelSwap(ports=ports, registry_path=registry_path, state_path=state_path,
                     emit=emit).step(sig, dry_run=dry_run)
