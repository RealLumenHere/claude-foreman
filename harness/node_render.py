"""node_render.py — `fleet-node render`: research/inference_nodes.yaml -> the three in-git targets.
research/INFERENCE_NODE_REGISTRY.md §3 (targets 2, 3, 5) and §8 T2-T4; wq#2250 (C2 of wq#2241).

  dispatcher/config.json          local_backends rows + the local-worker group
  research/models.yaml            the registry-owned local rows (pools: [local], status, ctx, trust)
  four LOCAL_POOL_MODELS tuples   orchestrator/judgment.py, harness/fworker.py, despag/admission.py,
                                  despag/app.py — kept as literal tuples on purpose (design §3)

Each renderer rewrites ONLY the lines between a `BEGIN GENERATED inference_nodes.yaml v1` and an
`END GENERATED` line, so hand-written neighbours survive. JSON has no comments, so config.json
carries the two sentinels as `_generated_begin` / `_generated_end` string keys (`_` keys are
notes to dispatch_config_lint and are skipped by dispatcher/local_capacity.py).

governor/governor_core.py and harness/concurrency.py are never written: both derive from models.yaml
(the governor unioned with its frozen floor, wq#2256) and need no renderer (a sixth copy is the bug
this work exists to kill).
The litellm gateway and key-allowlist targets are wq#2251 (C3).

CLI (exit 0 clean, 1 error, 2 drift under --check):
  python3 harness/fleet_node.py render [--check | --apply] [--path REGISTRY] [--root REPO]
"""
import ast
import difflib
import json
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
import inference_registry as ir  # noqa: E402

BEGIN = "BEGIN GENERATED inference_nodes.yaml v1"
END = "END GENERATED"

DISPATCHER_CONFIG = "dispatcher/config.json"
MODELS_YAML = "research/models.yaml"
PREFIX_FILES = ("orchestrator/judgment.py", "harness/fworker.py", "despag/admission.py", "despag/app.py")
LOCAL_GROUP = "local-worker"   # harness/concurrency.py LOCAL_GROUP_MODELS

STATUS = {"live": "LIVE-local", "draining": "draining", "cold": "cold", "down": "down"}
TRUST_STANDARD = ("standard-engineering + the mechanical-edit/probe-build local set. "
                  "PR-only + sonnet-5 verify gate. Lower --max-turns.")
TRUST_SMALL = "mechanical-edit only, gated. Offline-survival tier."
TRUST_GROUP = ("mechanical-edit + probe-build primary rung. PR-only + sonnet-5 verify gate "
               "(verify_pass.local_author_always). standard-engineering only behind "
               "local_standard_engineering.")


class RenderError(Exception):
    pass


def backends(reg):
    """One dict per served model on every non-retired node, in node order. A model's id is its
    explicit `id` or its family name; two rows with one id is a registry bug."""
    fams = reg.get("families") or {}
    out, seen = [], {}
    for node_name, node in (reg.get("nodes") or {}).items():
        node = node or {}
        if node.get("state") == "retired":
            continue
        for m in node.get("models") or []:
            fam_name = m.get("family")
            fam = fams.get(fam_name)
            if not isinstance(fam, dict):
                raise RenderError(f"nodes.{node_name}: model family {fam_name!r} is not in families")
            mid = m.get("id") or fam_name
            if mid in seen:
                raise RenderError(f"model id {mid!r} served by both {seen[mid]} and {node_name}")
            seen[mid] = node_name
            out.append({"id": mid, "node": node_name, "state": node.get("state", "live"),
                        "ip": node.get("tailscale_ip"), "family": fam, "model": m})
    return out


def _need(b, obj, key, where):
    if obj.get(key) is None:
        raise RenderError(f"{b['id']}: {where}.{key} is missing")
    return obj[key]


def _splice(text, inner, path):
    """`text` with the lines between its sentinels replaced by `inner` (a list of lines)."""
    lines = text.split("\n")
    begins = [i for i, ln in enumerate(lines) if BEGIN in ln]
    ends = [i for i, ln in enumerate(lines) if END in ln]
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        raise RenderError(f"{path}: needs exactly one '{BEGIN}' line before one '{END}' line")
    return "\n".join(lines[:begins[0] + 1] + inner + lines[ends[0]:])


def _read(root, rel):
    try:
        with open(os.path.join(root, rel)) as f:
            return f.read()
    except OSError as e:
        raise RenderError(f"cannot read {rel}: {e}") from e


def render_dispatcher(reg, root):
    rows, members = [], []
    for b in backends(reg):
        if b["state"] == "draining":   # design §5.2: a draining node's slots leave the dispatcher's view
            continue
        fam, m = b["family"], b["model"]
        url =f"http://{_need(b, b, 'ip', 'node.tailscale_ip')}:{_need(b, fam, 'port', 'family')}" \
              f"{_need(b, fam, 'health_path', 'family')}"
        row = {"health_url": url, "slots": _need(b, m, "slots", "model"),
               "served_ctx": _need(b, m, "served_ctx", "model"),
               "first_frame_s": _need(b, m, "first_frame_s", "model")}
        if m.get("prefill_tps") is not None:   # null = not yet measured; the lint keeps it optional
            row["prefill_tps"] = m["prefill_tps"]
        rows.append((b["id"], row))
        members.append(b["id"])
    if members:
        rows.append((LOCAL_GROUP, {"group": members}))
    # Every row ends in a comma: the `_generated_end` marker key that follows is the block's last.
    inner = [f"    {json.dumps(name)}: {json.dumps(row)}," for name, row in rows]
    new = _splice(_read(root, DISPATCHER_CONFIG), inner, DISPATCHER_CONFIG)
    try:
        cfg = json.loads(new)
    except ValueError as e:
        raise RenderError(f"{DISPATCHER_CONFIG}: rendered output is not JSON: {e}") from e
    fails, _ = _dispatcher_lint().validate_local_backends(cfg)
    if fails:
        raise RenderError(f"{DISPATCHER_CONFIG}: dispatch_config_lint rejects the render: {fails}")
    return new


def _dispatcher_lint():
    """dispatcher/dispatch_config_lint.py is the shape gate for target 2. Imported lazily: it pulls
    in harness/concurrency, and this module must stay importable without the dispatcher."""
    d = os.path.join(ir.REPO_ROOT, "dispatcher")
    if d not in sys.path:
        sys.path.insert(0, d)
    import dispatch_config_lint
    return dispatch_config_lint


def _model_row(b):
    fam, m = b["family"], b["model"]
    std = (m.get("capabilities") or {}).get("standard-engineering") or {}
    lines = [f"  - id: {b['id']}", f"    status: {STATUS.get(b['state'], 'down')}", "    pools: [local]"]
    if fam.get("context_tokens") is not None:
        lines.append(f"    context_tokens: {fam['context_tokens']}")
    lines.append(f"    served_ctx: {_need(b, m, 'served_ctx', 'model')}")
    lines.append("    efforts: []")
    lines.append(f"    trust: {json.dumps(TRUST_STANDARD if std.get('trusted') else TRUST_SMALL)}")
    return lines


def _group_row(bs):
    up = [b for b in bs if b["state"] not in ir.NO_SLOT_STATES] or bs
    ctx = min(_need(b, b["model"], "served_ctx", "model") for b in up)
    return [f"  - id: {LOCAL_GROUP}", "    status: LIVE-local", "    pools: [local]",
            f"    served_ctx: {ctx}", "    efforts: []", f"    trust: {json.dumps(TRUST_GROUP)}"]


def render_models(reg, root):
    bs = backends(reg)
    inner = [ln for b in bs for ln in _model_row(b)]
    if bs:
        inner += _group_row(bs)
    new = _splice(_read(root, MODELS_YAML), inner, MODELS_YAML)
    import yaml
    try:
        rows = (yaml.safe_load(new) or {}).get("models") or []
    except yaml.YAMLError as e:
        raise RenderError(f"{MODELS_YAML}: rendered output is not YAML: {e}") from e
    ids = [r.get("id") for r in rows if isinstance(r, dict)]
    want = [b["id"] for b in bs] + ([LOCAL_GROUP] if bs else [])
    if any(ids.count(i) != 1 for i in want):
        raise RenderError(f"{MODELS_YAML}: a registry-owned id is missing or duplicated: {want}")
    return new


def prefix_literal(prefixes):
    items = ", ".join(json.dumps(p) for p in prefixes)
    return f"LOCAL_POOL_MODELS = ({items},)" if len(prefixes) == 1 else f"LOCAL_POOL_MODELS = ({items})"


def render_prefix_lists(reg, root):
    """{path: text} for the four editable tuples. governor/governor_core.py derives its own."""
    prefixes = tuple(reg.get("governor_prefixes") or ())
    if not prefixes:
        raise RenderError("governor_prefixes: must be a non-empty list")
    out = {}
    for rel in PREFIX_FILES:
        new = _splice(_read(root, rel), [prefix_literal(prefixes)], rel)
        try:
            ast.parse(new)
        except SyntaxError as e:
            raise RenderError(f"{rel}: rendered output does not parse: {e}") from e
        out[rel] = new
    return out


def render_all(reg, root):
    """{repo-relative path: new full text} for every target, whether or not it changed."""
    out = {DISPATCHER_CONFIG: render_dispatcher(reg, root), MODELS_YAML: render_models(reg, root)}
    out.update(render_prefix_lists(reg, root))
    return out


def drift(reg, root):
    """{path: unified diff} for each target whose current bytes differ from the render."""
    out = {}
    for rel, new in render_all(reg, root).items():
        old = _read(root, rel)
        if old != new:
            out[rel] = "".join(difflib.unified_diff(
                old.splitlines(True), new.splitlines(True), f"a/{rel}", f"b/{rel}", n=1))
    return out


def apply(reg, root):
    """Write every drifted target; returns the paths written. Idempotent: a second run writes none."""
    changed = drift(reg, root)
    rendered = render_all(reg, root) if changed else {}
    for rel in changed:
        with open(os.path.join(root, rel), "w") as f:
            f.write(rendered[rel])
    return sorted(changed)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "render":
        args = args[1:]
    if any(a not in ("--check", "--apply", "--path", "--root") and a.startswith("--") for a in args) \
            or ("--check" in args and "--apply" in args):
        print(__doc__, file=sys.stderr)
        return 1

    def opt(name):
        return args[args.index(name) + 1] if name in args else None

    root = opt("--root") or os.path.normpath(ir.REPO_ROOT)
    try:
        reg = ir.load(opt("--path"))
        if "--apply" in args:
            written = apply(reg, root)
            print(f"fleet-node render: wrote {len(written)} file(s)" + "".join(f"\n  {p}" for p in written))
            return 0
        found = drift(reg, root)
    except (ir.RegistryError, RenderError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    for rel, diff in found.items():
        print(f"DRIFT {rel}\n{diff}")
    if "--check" in args:
        print(f"fleet-node render --check: {len(found)} target(s) drifted from the registry")
        return 2 if found else 0
    print(f"fleet-node render (dry run): {len(found)} target(s) would change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
