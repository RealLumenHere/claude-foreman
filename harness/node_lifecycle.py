"""node_lifecycle.py — `fleet-node add` and `fleet-node retire`.
research/INFERENCE_NODE_REGISTRY.md §5, §8 T5/T6; wq#2253 (C4 of wq#2241).

  python3 harness/fleet_node.py add    NODE --family F [--slots N] [--power-policy P] [--source NODE]
                                       [--yes] [--dry-run] [--rollback]
  python3 harness/fleet_node.py retire NODE [--family F] [--power off|leave] [--force] [--yes]
                                       [--dry-run] [--rollback] [--drain-deadline-s S]

Both verbs run the same six phases: precheck (read-only) -> dry-run diff across all five targets
(gateway litellm_config.yaml, scoped key allowlist, dispatcher/config.json, research/models.yaml, the
four LOCAL_POOL_MODELS tuples) -> serve|drain -> health probe -> bench|freeze -> apply + verify.
Without --yes the run stops after the diff. Exit 0 = done, or a bench NO-GO (the node is left at
state=down with the numbers recorded — that is a result, not a failure); exit 1 = refused or failed,
with every target restored to its pre-run bytes.

All fleet side effects go through a Ports object (node_ports.SystemPorts by default).
"""
import argparse
import datetime
import difflib
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import inference_registry as ir  # noqa: E402
import node_render as nr  # noqa: E402
import node_render_gateway as ng  # noqa: E402
import registry_edit as ed  # noqa: E402
from node_ports import PortError, SystemPorts  # noqa: E402

BLOCK_REL = "ops/inference-registry/litellm_local_block.yaml"
NODE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# QWEN36_LOCAL_WORKER_SERVING.md:115-124 — a family may override with `bench_gate: {min_*_tps}`.
GATE = {"min_prefill_tps": 50, "min_decode_tps": 9}
PROVISIONAL_CTX, PROVISIONAL_FIRST_FRAME_S = 8192, 1800   # placeholders until the bench measures them
PROBE_DEADLINE_S, PROBE_INTERVAL_S = 300, 5
GATEWAY_DEADLINE_S, GATEWAY_INTERVAL_S = 180, 5
DRAIN_DEADLINE_S, DRAIN_POLL_S = 1800, 10
RAM_TOLERANCE = 0.10   # MemTotal is below installed RAM
NEW_FAMILY_TICKET = "wq#2256"
LIVE_LIKE = ("live", "draining", "cold")


class PhaseError(Exception):
    pass


class Ctx:
    def __init__(self, ports=None, registry=None, root=None, litellm_config=None, out=print, state_dir=None,
                 cooldown_path=None, yes=False, dry_run=False, today=None):
        self.ports = ports or SystemPorts()
        self.root = root or os.path.normpath(ir.REPO_ROOT)
        self.registry = registry or (os.path.join(root, "research", "inference_nodes.yaml") if root
                                     else ir.REGISTRY_YAML)
        self.litellm_config = litellm_config or ng.LITELLM_CONFIG
        self.out = out
        runtime = os.environ.get("FOREMAN_RUNTIME_DIR", os.path.join(ir.REPO_ROOT, "dispatcher", "runtime"))
        self.state_dir = state_dir or os.environ.get("FOREMAN_FLEET_NODE_STATE") or os.path.join(runtime, "fleet-node")
        self.cooldown_path = cooldown_path
        self.yes, self.dry_run = yes, dry_run
        self.today = today or datetime.date.today().isoformat()


# --- files, registry text, snapshots -------------------------------------------------------------

def _read(path):
    with open(path) as f:
        return f.read()


def _write(path, text):
    if os.path.exists(path):
        ng._atomic_write(path, text)
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def _parse(text):
    import yaml
    try:
        reg = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PhaseError(f"registry edit produced invalid YAML: {e}") from e
    if not isinstance(reg, dict):
        raise PhaseError("registry edit produced a non-mapping document")
    return reg


def _tracked(ctx):
    """Every file the run may rewrite: the in-git targets, the generated gateway block, the live
    gateway config and the registry itself."""
    rels = (nr.DISPATCHER_CONFIG, nr.MODELS_YAML) + nr.PREFIX_FILES + (BLOCK_REL,)
    return [os.path.join(ctx.root, r) for r in rels] + [ctx.litellm_config, ctx.registry]


def take_snapshot(ctx, **meta):
    paths = _tracked(ctx)
    return {"files": {p: _read(p) for p in paths if os.path.exists(p)},
            "absent": [p for p in paths if not os.path.exists(p)],
            "key_models": ctx.ports.key_models(), "meta": meta}


def restore_snapshot(ctx, snap):
    """Put all five targets (and the registry) back to their pre-run bytes; -> what was restored."""
    restored = []
    for path, text in snap["files"].items():
        if not os.path.exists(path) or _read(path) != text:
            _write(path, text)
            restored.append(path)
    for path in snap["absent"]:
        if os.path.exists(path):
            os.remove(path)
            restored.append(path)
    if ctx.ports.key_models() != snap["key_models"]:
        ctx.ports.restore_key_models(snap["key_models"])
        restored.append("key allowlist")
    if ctx.litellm_config in restored:
        ctx.ports.restart_gateway()
    return restored


def _snap_path(ctx, op, node, family):
    return os.path.join(ctx.state_dir, f"{op}-{node}-{family or 'all'}.json")


def save_snapshot(ctx, op, node, family, snap):
    path = _snap_path(ctx, op, node, family)
    os.makedirs(ctx.state_dir, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # the gateway config may hold keys
    with os.fdopen(fd, "w") as f:
        json.dump(snap, f)


def load_snapshot(ctx, op, node, family):
    try:
        with open(_snap_path(ctx, op, node, family)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# --- five targets: diff and apply ----------------------------------------------------------------

def _diff(old, new, label):
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        f"a/{label}", f"b/{label}", n=1))


def five_diffs(ctx, reg):
    """{target label: unified diff} of every target's current bytes against the render of `reg`."""
    old_cfg = _read(ctx.litellm_config)
    live = ctx.ports.key_models()
    drift = nr.drift(reg, ctx.root)
    return {
        "1/5 gateway litellm_config.yaml": _diff(
            old_cfg, ng.splice_block(old_cfg, ng.render_litellm(reg), ng.owned_names(reg)), "litellm_config.yaml"),
        "2/5 scoped key allowlist": _diff("\n".join(live) + "\n",
                                           "\n".join(ng.merge_allowlist(live, reg)) + "\n", "foreman-local-rung.models"),
        f"3/5 {nr.DISPATCHER_CONFIG}": drift.get(nr.DISPATCHER_CONFIG, ""),
        f"4/5 {nr.MODELS_YAML}": drift.get(nr.MODELS_YAML, ""),
        "5/5 LOCAL_POOL_MODELS tuples": "".join(drift.get(rel, "") for rel in nr.PREFIX_FILES),
    }


def print_diffs(ctx, diffs):
    for label, diff in diffs.items():
        ctx.out(f"== {label} ==")
        ctx.out(diff.rstrip("\n") if diff else "(no change)")


def apply_targets(ctx, reg, gateway=True):
    """Render the registry into the targets. `gateway=False` (a bench NO-GO) leaves the live gateway and
    key allowlist alone so no traffic is routed to a node that failed its gate."""
    written = nr.apply(reg, ctx.root)
    _write(os.path.join(ctx.root, BLOCK_REL), ng.render_litellm(reg))
    if not gateway:
        return written
    changed = ng.apply_litellm(reg, ctx.litellm_config)["changed"]
    ctx.ports.apply_key_allowlist(reg)
    if changed:
        ctx.ports.restart_gateway()
    return written


def _poll(ctx, fn, deadline_s, interval_s):
    """Seconds until `fn()` first answers true, or None at the deadline."""
    t0 = ctx.ports.clock()
    while True:
        if fn():
            return ctx.ports.clock() - t0
        if ctx.ports.clock() - t0 >= deadline_s:
            return None
        ctx.ports.sleep(interval_s)


def _capacity(ctx):
    """The dispatcher's own LocalCapacity over the rendered config — the view a real pass would take."""
    sys.path.insert(0, os.path.join(ir.REPO_ROOT, "dispatcher"))
    import local_capacity
    cfg = json.loads(_read(os.path.join(ctx.root, nr.DISPATCHER_CONFIG)))
    return local_capacity.LocalCapacity(cfg.get("local_backends"), ctx.ports.running_local_models,
                                        probe=ctx.ports.probe, cooldown_path=ctx.cooldown_path), cfg


def _lint_errors(ctx, reg):
    ceiling = ir.dispatcher_local_ceiling(os.path.join(ctx.root, nr.DISPATCHER_CONFIG))
    return [str(f) for f in ir.lint(reg, inventory=ctx.ports, local_ceiling=ceiling) if f.level == "error"]


def _verify_targets(ctx, reg):
    errs = _lint_errors(ctx, reg)
    if errs:
        raise PhaseError("lint: " + "; ".join(errs))
    drifted = nr.drift(reg, ctx.root)
    if drifted:
        raise PhaseError(f"render --check drift after apply: {sorted(drifted)}")


# --- add -----------------------------------------------------------------------------------------

def _model_id(family, node):
    return f"{family}.{node}"


def _pick_source(reg, node, family, explicit):
    if explicit:
        return explicit
    order = {"live": 0, "draining": 1, "cold": 2, "down": 3}
    hits = [(order.get(nd.get("state"), 9), n) for n, nd in (reg.get("nodes") or {}).items()
            if n != node and isinstance(nd, dict) and nd.get("state") != "retired"
            and any(m.get("family") == family for m in nd.get("models") or [])]
    if hits:
        return min(hits)[1]
    own = (reg.get("nodes") or {}).get(node) or {}
    return node if any(m.get("family") == family for m in own.get("models") or []) else None   # a revive


def _ram_ok(live, declared):
    return abs(live - declared) <= max(2, RAM_TOLERANCE * declared)


def _check_hardware(ctx, node, existing, fails):
    ports = ctx.ports
    live = ports.hardware(node)
    if live["cores"] is None:
        fails.append(f"ssh to {node} did not answer (or lscpu/meminfo unreadable)")
        return
    declared = ({"cores": existing.get("cores"), "ram_gb": existing.get("ram_gb")} if existing
                else ports.declared_hardware(node))
    if declared["cores"] is None or declared["ram_gb"] is None:
        fails.append(f"no declared cores/RAM for {node} in the registry or fleet-inv to check against")
    elif live["cores"] != declared["cores"] or not _ram_ok(live["ram_gb"], declared["ram_gb"]):
        fails.append(f"hardware mismatch on {node}: lscpu/meminfo say {live['cores']} cores / {live['ram_gb']} GiB, "
                     f"declared {declared['cores']} / {declared['ram_gb']} (real SKU conflicts exist: "
                     "research/QWEN36_LOCAL_WORKER_SERVING.md:81-85)")
    return live, declared


def _check_blob(ctx, node, family, fam, source, fails):
    ports = ctx.ports
    if source is None:
        if fam["serving"] != "ollama":
            fails.append(f"no other node serves {family}: pass --source NODE to compare the blob sha256 against")
        return
    try:
        want, have = ports.blob_sha(source, family, fam, source), ports.blob_sha(node, family, fam, source)
    except Exception as e:  # noqa: BLE001 — a blob we cannot hash is a blob we cannot vouch for
        fails.append(f"blob sha256 unreadable: {e}")
        return
    if want is None:
        fails.append(f"source node {source} has no {family} blob")
    elif have is None:
        if fam["serving"] != "ollama":   # ollama pulls it in phase 3; a GGUF must already be copied (§2.1)
            fails.append(f"blob missing on {node}: copy it from {source} first "
                         "(research/QWEN36_LOCAL_WORKER_SERVING.md §2.1), then re-run")
    elif have != want:
        fails.append(f"blob sha256 mismatch: {source}={want[:16]}… {node}={have[:16]}…")


def precheck_add(ctx, reg, node, family, slots, power_policy, source):
    """(failures, info). Reads only — ssh, fleet-inv, the gateway's key list — and changes nothing."""
    fails, ports = [], ctx.ports
    fams = reg.get("families") or {}
    fam = fams.get(family)
    if fam is None:
        return [f"family {family!r} is not in the registry (families: {sorted(fams)})"], {}
    prefixes = list(reg.get("governor_prefixes") or [])
    if not any(family.startswith(p) for p in prefixes):
        fails.append(f"family {family!r} starts with none of the governor prefixes {prefixes}: the governor would "
                     "bill it to Anthropic as a cloud model (governor/governor_core.py:97, design §4). "
                     f"Escape hatch: {NEW_FAMILY_TICKET}")
    if not NODE_RE.match(node):
        fails.append(f"node name {node!r} breaks design §4: the model id is <family>.<node>, so the node must match "
                     "[a-z0-9][a-z0-9-]* ('@' is the effort separator, dispatcher/local_capacity.py:51)")
    if power_policy not in ir.POWER_POLICIES:
        fails.append(f"power policy {power_policy!r} must be one of {ir.POWER_POLICIES}")
    if not isinstance(slots, int) or slots < 1:
        fails.append(f"--slots must be a positive integer, got {slots!r}")
    existing = (reg.get("nodes") or {}).get(node)
    if existing:
        state = existing.get("state")
        if state in LIVE_LIKE:
            fails.append(f"node {node} is already {state}; add takes a new node or a down/retired one")
        elif not any(m.get("family") == family for m in existing.get("models") or []):
            fails.append(f"node {node} has no {family} row; adding a second family to an existing node is not "
                         "supported (state is per node, design §2.1)")
    info = {"fam": fam, "existing": existing, "source": _pick_source(reg, node, family, source)}
    try:
        plugs = ports.plugs()
        ip = ports.tailscale_ip(node)
    except OSError as e:
        return fails + [f"fleet-inv unavailable: {e}"], info
    if node not in plugs:
        fails.append(f"node {node!r} has no plug_id in `fleet-inv plugs`")
    if not ip:
        fails.append(f"node {node!r} has no tailscale_ip in fleet-inv")
    info.update(plug=plugs.get(node), ip=ip)
    if fails:
        return fails, info
    hw = _check_hardware(ctx, node, existing, fails)
    if hw:
        info["hardware"] = hw
    _check_blob(ctx, node, family, fam, info["source"], fails)
    if fam["serving"] == "llama-server" and not ports.port_free(node, fam["port"]):
        fails.append(f"port {fam['port']} is not free on {node}")
    try:
        ports.key_models()
    except Exception as e:  # noqa: BLE001
        fails.append(f"cannot read the scoped key allowlist: {e}")
    return fails, info


def propose_add(ctx, text, node, family, slots, power_policy, info):
    """The registry text with the node added (state=down, provisional numbers) or revived."""
    if info["existing"]:
        text = ed.set_field(text, node, "state", "down")
        text = ed.set_field(text, node, "power_policy", power_policy)
        return ed.set_field(text, node, "slots", slots, family=family)
    cores, declared = info["hardware"]
    fields = {"state": "down", "tailscale_ip": info["ip"], "plug_id": info["plug"], "power_policy": power_policy,
              "power_reason": f"fleet-node add {ctx.today}: {power_policy}", "cores": cores["cores"],
              "ram_gb": declared["ram_gb"], "idle_watts": None}
    model = {"family": family, "id": _model_id(family, node), "resident": True, "slots": slots,
             "served_ctx": PROVISIONAL_CTX, "first_frame_s": PROVISIONAL_FIRST_FRAME_S, "prefill_tps": None,
             "decode_tps": None, "measured_at": None, "load_s": None}
    return ed.append_node(text, node, fields, model)


def bench_verdict(bench, fam):
    """(go, misses) against the wq#2127 go/no-go gate."""
    gate = {**GATE, **(fam.get("bench_gate") or {})}
    misses = []
    if (bench.get("prefill_tps") or 0) < gate["min_prefill_tps"]:
        misses.append(f"warm prefill {bench.get('prefill_tps')} tok/s < {gate['min_prefill_tps']}")
    if (bench.get("decode_tps") or 0) < gate["min_decode_tps"]:
        misses.append(f"decode {bench.get('decode_tps')} tok/s < {gate['min_decode_tps']}")
    if not bench.get("concurrent_ok"):
        misses.append("4 concurrent 1k-token requests did not all complete")
    if bench.get("oom"):
        misses.append("OOM in the serving journal")
    if not bench.get("served_ctx"):
        misses.append("served_ctx unreadable from the backend")
    return not misses, misses + list(bench.get("notes") or [])


def record_bench(ctx, text, node, family, bench, go):
    """The registry text with the measured numbers written into the row; the node goes live only on GO."""
    updates = {"prefill_tps": bench.get("prefill_tps"), "decode_tps": bench.get("decode_tps"),
               "served_ctx": bench.get("served_ctx"), "load_s": bench.get("load_s"), "measured_at": ctx.today}
    if bench.get("served_ctx") and bench.get("prefill_tps"):   # fworker's budget gate (harness/fworker.py:2450)
        updates["first_frame_s"] = math.ceil(bench["served_ctx"] / bench["prefill_tps"] * 2)
    for key, value in updates.items():
        if value is not None:
            text = ed.set_field(text, node, key, value, family=family)
    return ed.set_field(text, node, "state", "live" if go else "down")


def _mirror_bench(ctx, node, family, bench, go, misses):
    pred = re.sub(r"-", "_", re.sub(r"-local$", "", family)) + "_bench"
    summary = (f"fleet-node bench {ctx.today}: prefill {bench.get('prefill_tps')} tok/s, decode "
               f"{bench.get('decode_tps')} tok/s, served_ctx {bench.get('served_ctx')}, load_s "
               f"{bench.get('load_s')}, gate {'GO' if go else 'NO-GO: ' + '; '.join(misses)}")
    try:
        ctx.ports.state_write(node, pred, summary)
        ctx.out(f"lumen_state {node}.{pred} written")
    except Exception as e:  # noqa: BLE001 — the registry row is the record of truth; say so and go on
        ctx.out(f"WARN lumen_state mirror {node}.{pred} failed: {e}")


def _fail_add(ctx, snap, node, family, fam, why):
    ctx.out(f"FAIL {why}")
    if snap is not None:
        try:
            ctx.out("rolled back: " + (", ".join(os.path.basename(p) for p in restore_snapshot(ctx, snap)) or "nothing to restore"))
        except Exception as e:  # noqa: BLE001
            ctx.out(f"ROLLBACK INCOMPLETE: {e}")
    try:
        ctx.ports.unserve(node, family, fam)
    except Exception as e:  # noqa: BLE001
        ctx.out(f"WARN could not stop the new unit on {node}: {e}")
    return 1


def add(ctx, node, family, slots=2, power_policy="warm", source=None):
    ports, out = ctx.ports, ctx.out
    text = _read(ctx.registry)
    reg = _parse(text)
    out(f"[1/6] precheck: add {family} on {node}")
    fails, info = precheck_add(ctx, reg, node, family, slots, power_policy, source)
    if not fails:
        try:
            proposed = propose_add(ctx, text, node, family, slots, power_policy, info)
            preg = _parse(proposed)
            nr.backends(preg)
            ceiling = ir.dispatcher_local_ceiling(os.path.join(ctx.root, nr.DISPATCHER_CONFIG))
            before = {str(f) for f in ir.lint(reg, inventory=ports, local_ceiling=ceiling)}
            live_variant = _parse(ed.set_field(proposed, node, "state", "live"))
            fails += [f"lint: {f}" for f in ir.lint(live_variant, inventory=ports, local_ceiling=ceiling)
                      if f.level == "error" and str(f) not in before]
        except (PhaseError, ed.EditError, nr.RenderError) as e:
            fails.append(str(e))
    if fails:
        for f in fails:
            out(f"PRECHECK FAIL: {f}")
        out("nothing was changed")
        return 1
    out("[2/6] dry-run diff across the five targets (proposed: state=down, provisional numbers until the bench)")
    try:
        print_diffs(ctx, five_diffs(ctx, preg))
    except (nr.RenderError, ng.GatewayError) as e:
        out(f"FAIL render: {e}")
        return 1
    if ctx.dry_run or not ctx.yes:
        out("dry run: nothing written. Re-run with --yes to apply.")
        return 0
    fam = info["fam"]
    ip = info["ip"]
    model_id = next(b["id"] for b in nr.backends(preg) if b["node"] == node)   # a revived row keeps its own id
    snap = take_snapshot(ctx, op="add", node=node, family=family)
    save_snapshot(ctx, "add", node, family, snap)
    try:
        out(f"[3/6] serve {family} on {node}")
        ports.serve(node, family, fam, slots, info["source"])
        url = f"http://{ip}:{fam['port']}{fam['health_path']}"
        out(f"[4/6] health probe {url} (deadline {PROBE_DEADLINE_S}s)")
        up_s = _poll(ctx, lambda: ports.probe(url) and ports.model_ready(node, fam), PROBE_DEADLINE_S, PROBE_INTERVAL_S)
        if up_s is None:
            raise PhaseError(f"health probe did not pass within {PROBE_DEADLINE_S}s")
        if not ports.backend_complete(ip, fam):
            raise PhaseError("the backend answered its probe but a real completion failed")
        out("[5/6] bench (warm ~4k-token prompt, 4x1k concurrent, OOM scan)")
        bench = ports.bench(node, ip, family, fam, slots, round(up_s, 1))
    except Exception as e:  # noqa: BLE001 — nothing is applied yet; stop the unit and report
        return _fail_add(ctx, None, node, family, fam, f"phase 3-5: {e}")
    go, misses = bench_verdict(bench, fam)
    out(f"bench: prefill {bench.get('prefill_tps')} tok/s, decode {bench.get('decode_tps')} tok/s, served_ctx "
        f"{bench.get('served_ctx')}, load_s {bench.get('load_s')} -> {'GO' if go else 'NO-GO'}")
    for m in misses:
        out(f"  miss: {m}")
    _mirror_bench(ctx, node, family, bench, go, misses)
    try:
        out("[6/6] apply + verify")
        new_text = record_bench(ctx, proposed, node, family, bench, go)
        _write(ctx.registry, new_text)
        final = _parse(new_text)
        written = apply_targets(ctx, final, gateway=go)
        out("wrote: " + ", ".join(written))
        if go:
            ports.quarantine(model_id, clear=True)
            _verify_targets(ctx, final)
            if _poll(ctx, lambda: ports.gateway_complete(family), GATEWAY_DEADLINE_S, GATEWAY_INTERVAL_S) is None:
                raise PhaseError("no completion through the gateway after the restart")
            cap, _ = _capacity(ctx)
            if cap.free_slots(model_id) < 1:
                raise PhaseError(f"dispatcher view shows no free slot for {model_id}")
    except Exception as e:  # noqa: BLE001 — phase 6 failure: all five targets back to pre-run bytes
        return _fail_add(ctx, snap, node, family, fam, f"phase 6: {e}")
    if go:
        out(f"OK {node} is live: {model_id}, {slots} slots")
        return 0
    out(f"NO-GO: {node} stays at state=down with the numbers recorded; the unit is stopped and the backend quarantined")
    try:
        ports.unserve(node, family, fam)
        ports.quarantine(model_id)
    except Exception as e:  # noqa: BLE001
        out(f"WARN could not fully quiesce {node}: {e}")
    return 0


# --- retire --------------------------------------------------------------------------------------

def _in_flight(ctx, ids):
    sys.path.insert(0, os.path.join(ir.REPO_ROOT, "dispatcher"))
    import local_capacity
    return sum(1 for m in ctx.ports.running_local_models() if m and local_capacity.base_name(m) in ids)


def precheck_retire(ctx, reg, node, family, force):
    fails = []
    nd = (reg.get("nodes") or {}).get(node)
    if not isinstance(nd, dict):
        return [f"node {node!r} is not in the registry"], {}
    if nd.get("state") == "retired":
        return [f"node {node} is already retired"], {}
    models = nd.get("models") or []
    if family and not any(m.get("family") == family for m in models):
        fails.append(f"node {node} serves no {family} row")
    elif family and len(models) > 1:
        fails.append("per-model retire is not supported: state is per node (design §2.1); retire the whole node")
    if nd.get("power_policy") == "always-on" and not force:
        fails.append(f"node {node} is always-on ({nd.get('power_reason')}): refusing without --force "
                     "(Frank requirement 5)")
    ids = {b["id"] for b in nr.backends(reg) if b["node"] == node}
    cap, cfg = _capacity(ctx)
    members = ((cfg.get("local_backends") or {}).get(nr.LOCAL_GROUP) or {}).get("group") or []
    healthy = [m for m in members if cap.knows(m) and cap.healthy(m)]
    if any(m in ids for m in healthy) and not [m for m in healthy if m not in ids]:
        fails.append(f"node {node} is the last healthy member of {nr.LOCAL_GROUP} ({healthy}): retiring it would "
                     "render the group empty and park every local ticket")
    try:
        ctx.ports.key_models()
    except Exception as e:  # noqa: BLE001
        fails.append(f"cannot read the scoped key allowlist: {e}")
    return fails, {"ids": ids, "prior": nd.get("state", "live")}


def _revert_state(ctx, node, prior):
    """Rollback of a retire: the row was kept, so `state: <prior>` plus one render puts everything back."""
    text = ed.set_field(_read(ctx.registry), node, "state", prior)
    if ed.get_field(text, node, "retired_at") is not None:
        text = ed.set_field(text, node, "retired_at", None)
    _write(ctx.registry, text)
    apply_targets(ctx, _parse(text), gateway=True)


def _fail_retire(ctx, node, snap, why):
    """Automatic rollback restores the pre-run bytes rather than re-rendering: the render path may be
    the very thing that just failed (a key allowlist that would not update)."""
    ctx.out(f"FAIL {why}")
    try:
        restore_snapshot(ctx, snap)
        ctx.out(f"rolled back: {node} is {snap['meta']['prior']} again; no running worker was touched")
    except Exception as e:  # noqa: BLE001
        ctx.out(f"ROLLBACK INCOMPLETE: {e}")
    return 1


def retire(ctx, node, family=None, power="leave", force=False, drain_deadline_s=DRAIN_DEADLINE_S,
           poll_s=DRAIN_POLL_S):
    ports, out = ctx.ports, ctx.out
    text = _read(ctx.registry)
    reg = _parse(text)
    out(f"[1/6] precheck: retire {node}")
    try:
        fails, info = precheck_retire(ctx, reg, node, family, force)
    except (nr.RenderError, PhaseError) as e:
        fails, info = [str(e)], {}
    if fails:
        for f in fails:
            out(f"PRECHECK FAIL: {f}")
        out("nothing was changed")
        return 1
    out("[2/6] dry-run diff across the five targets (proposed: state=retired, row kept)")
    proposed = ed.set_field(ed.set_field(text, node, "state", "retired"), node, "retired_at", ctx.today)
    try:
        print_diffs(ctx, five_diffs(ctx, _parse(proposed)))
    except (nr.RenderError, ng.GatewayError) as e:
        out(f"FAIL render: {e}")
        return 1
    if ctx.dry_run or not ctx.yes:
        out("dry run: nothing written. Re-run with --yes to apply.")
        return 0
    ids, prior = info["ids"], info["prior"]
    snap = take_snapshot(ctx, op="retire", node=node, family=family, prior=prior)
    save_snapshot(ctx, "retire", node, family, snap)
    try:
        out(f"[3/6] drain: {node} -> draining; no new work is admitted (deadline {drain_deadline_s}s)")
        drained = ed.set_field(text, node, "state", "draining")
        _write(ctx.registry, drained)
        apply_targets(ctx, _parse(drained), gateway=True)
    except Exception as e:  # noqa: BLE001
        return _fail_retire(ctx, node, snap, f"phase 3: {e}")
    if _poll(ctx, lambda: _in_flight(ctx, ids) == 0, drain_deadline_s, poll_s) is None:
        return _fail_retire(ctx, node, snap, f"{_in_flight(ctx, ids)} unit(s) still running on {node} after "
                                             f"{drain_deadline_s}s (never killing a worker to retire a box)")
    out("[4/6] probe: zero in-flight units on the node")
    out("[5/6] bench skipped: the row's measured numbers are frozen as they stand")
    try:
        out("[6/6] apply + verify")
        _write(ctx.registry, proposed)
        final = _parse(proposed)
        written = apply_targets(ctx, final, gateway=True)
        out("wrote: " + ", ".join(written))
        _verify_targets(ctx, final)
        cap, _ = _capacity(ctx)
        if any(cap.knows(i) for i in ids):
            raise PhaseError("the dispatcher view still lists the retired backend")
    except Exception as e:  # noqa: BLE001
        return _fail_retire(ctx, node, snap, f"phase 6: {e}")
    out(f"OK {node} retired (row kept with its measured numbers)")
    if power == "off":
        try:
            ports.power_off(node)
        except (NotImplementedError, PortError) as e:
            out(f"WARN --power off skipped: {e}")
    return 0


def rollback(ctx, op, node, family):
    """Undo the last completed add or retire of `node` from its saved snapshot."""
    snap = load_snapshot(ctx, op, node, family)
    if snap is None:
        ctx.out(f"no saved {op} run for {node} ({_snap_path(ctx, op, node, family)})")
        return 1
    if op == "retire":
        _revert_state(ctx, node, snap["meta"].get("prior", "live"))
        ctx.out(f"rolled back: {node} is {snap['meta'].get('prior', 'live')} again (one render)")
        return 0
    restored = restore_snapshot(ctx, snap)
    fam = (_parse(_read(ctx.registry)).get("families") or {}).get(family)
    if fam:
        ctx.ports.unserve(node, family, fam)
    ctx.out("rolled back: " + (", ".join(os.path.basename(p) for p in restored) or "nothing to restore"))
    return 0


# --- CLI -----------------------------------------------------------------------------------------

def main(argv=None, ports=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ("add", "retire"):
        print(__doc__, file=sys.stderr)
        return 1
    verb = args[0]
    ap = argparse.ArgumentParser(prog=f"fleet-node {verb}")
    ap.add_argument("node")
    ap.add_argument("--family", required=verb == "add")
    ap.add_argument("--path", "--registry", dest="registry")
    ap.add_argument("--root")
    ap.add_argument("--litellm-config")
    ap.add_argument("--yes", action="store_true", help="go past the dry-run diff")
    ap.add_argument("--dry-run", action="store_true", help="stop after the diff even with --yes")
    ap.add_argument("--rollback", action="store_true", help="undo the last completed run from its snapshot")
    if verb == "add":
        ap.add_argument("--slots", type=int, default=2)
        ap.add_argument("--power-policy", default="warm", choices=ir.POWER_POLICIES)
        ap.add_argument("--source", help="node whose blob and unit are the template (default: a node serving the family)")
    else:
        ap.add_argument("--power", choices=("off", "leave"), default="leave")
        ap.add_argument("--force", action="store_true", help="retire an always-on node")
        ap.add_argument("--drain-deadline-s", type=int, default=DRAIN_DEADLINE_S)
    a = ap.parse_args(args[1:])
    ctx = Ctx(ports=ports, registry=a.registry, root=a.root, litellm_config=a.litellm_config, yes=a.yes,
              dry_run=a.dry_run)
    try:
        if a.rollback:
            return rollback(ctx, verb, a.node, a.family)
        if verb == "add":
            return add(ctx, a.node, a.family, a.slots, a.power_policy, a.source)
        return retire(ctx, a.node, a.family, a.power, a.force, a.drain_deadline_s)
    except (ir.RegistryError, OSError, PhaseError, ed.EditError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
