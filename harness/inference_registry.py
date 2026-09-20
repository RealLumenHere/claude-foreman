"""inference_registry.py — loader + lint for research/inference_nodes.yaml.
research/INFERENCE_NODE_REGISTRY.md §2 (schema) and §4 (naming rule); wq#2248 (C1 of wq#2241).

Pure stdlib + PyYAML, imports nothing from dispatcher/ or orchestrator/. Governor-adjacent
modules will import this at import time, so — like harness/concurrency.py `_pool_models` —
nothing here may raise at import: PyYAML is imported inside load(), and the never-raise
helper `local_model_ids()` falls back to the caller's tuple when the file or PyYAML is unusable.

CLI (exit 0 clean, 1 lint errors):  python3 harness/inference_registry.py lint [--path P]
"""
import ast
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
# FOREMAN_INFERENCE_NODES mirrors FOREMAN_MODELS_YAML (harness/concurrency.py:44) for tests.
REGISTRY_YAML = os.environ.get(
    "FOREMAN_INFERENCE_NODES", os.path.join(REPO_ROOT, "research", "inference_nodes.yaml"))
GOVERNOR_CORE = os.path.join(REPO_ROOT, "governor", "governor_core.py")
DISPATCH_CONFIG = os.path.join(REPO_ROOT, "dispatcher", "config.json")
FLEET_INV = os.environ.get("FOREMAN_FLEET_INV", "/home/lumen/bin/fleet-inv")

STATES = ("live", "draining", "cold", "down", "retired")
POWER_POLICIES = ("cold", "warm", "always-on")
SERVING = ("llama-server", "ollama")
SWAPS = ("unit", "implicit")
# States whose models are not expected to hold a dispatcher slot (a down node's probe fails).
NO_SLOT_STATES = ("down", "retired")


class RegistryError(Exception):
    pass


def load(path=None):
    """Parsed registry dict. Raises RegistryError on a missing file, bad YAML or missing PyYAML."""
    try:
        import yaml
        with open(path or REGISTRY_YAML) as f:
            reg = yaml.safe_load(f)
    except Exception as e:  # noqa: BLE001 — yaml.YAMLError is not a ValueError
        raise RegistryError(f"cannot load {path or REGISTRY_YAML}: {e}") from e
    if not isinstance(reg, dict):
        raise RegistryError(f"{path or REGISTRY_YAML}: top level must be a mapping")
    return reg


def local_model_ids(fallback=(), path=None):
    """Family ids in the registry; `fallback` on any failure. Never raises."""
    try:
        found = tuple((load(path).get("families") or {}).keys())
    except Exception:  # noqa: BLE001 — must be import-safe for governor-adjacent callers
        return tuple(fallback)
    return found or tuple(fallback)


def model_ids(reg):
    """Every id the registry serves: each family key plus any explicit per-model `id`."""
    ids = list((reg.get("families") or {}).keys())
    for node in (reg.get("nodes") or {}).values():
        for m in (node or {}).get("models") or []:
            if isinstance(m, dict) and m.get("id"):
                ids.append(m["id"])
    return tuple(dict.fromkeys(ids))


def governor_prefixes(path=None):
    """The governor's LOCAL_POOL_MODELS tuple, parsed with ast (governor_core is import-fragile
    by design and is never imported). None when the file or the assignment is unreadable."""
    try:
        with open(path or GOVERNOR_CORE) as f:
            tree = ast.parse(f.read())
        for node in tree.body:
            if (isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "LOCAL_POOL_MODELS" for t in node.targets)):
                return tuple(ast.literal_eval(node.value))
    except (OSError, SyntaxError, ValueError):
        pass
    return None


def dispatcher_local_ceiling(path=None):
    try:
        with open(path or DISPATCH_CONFIG) as f:
            return int(json.load(f)["local_ceiling"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


class FleetInventory:
    """Reads plug_id / tailscale_ip through the fleet-inv CLI, the authority (§2.2). Methods
    return None when the inventory has no answer; raise OSError when fleet-inv cannot run."""

    def __init__(self, cmd=None):
        self.cmd = [sys.executable, cmd or FLEET_INV]

    def _run(self, *args):
        p = subprocess.run(self.cmd + list(args), capture_output=True, text=True, timeout=30)
        return p.returncode, p.stdout

    def plugs(self):
        rc, out = self._run("plugs")
        if rc != 0:
            raise OSError(f"fleet-inv plugs exited {rc}")
        rows = (line.split("\t") for line in out.splitlines() if line.strip())
        return {r[1]: r[0] for r in rows if len(r) >= 2}

    def get(self, name, key):
        rc, out = self._run("get", name, key)
        return out.strip() if rc == 0 and out.strip() else None

    def tailscale_ip(self, name):
        return self.get(name, "network.tailscale_ip")


class Finding:
    def __init__(self, level, rule, msg):
        self.level, self.rule, self.msg = level, rule, msg

    def __str__(self):
        return f"{self.level.upper()} [{self.rule}] {self.msg}"


def _num_ok(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def _lint_families(reg, prefixes, out):
    fams = reg.get("families") or {}
    if not isinstance(fams, dict) or not fams:
        out.append(Finding("error", "schema", "families: must be a non-empty mapping"))
        return
    by_prefix = {}
    for fid, fam in fams.items():
        if not isinstance(fam, dict):
            out.append(Finding("error", "schema", f"families.{fid}: must be a mapping"))
            continue
        for key in ("blob", "serving", "port", "health_path", "swap"):
            if fam.get(key) in (None, ""):
                out.append(Finding("error", "schema", f"families.{fid}: missing {key}"))
        if fam.get("serving") not in SERVING + (None,):
            out.append(Finding("error", "schema", f"families.{fid}: serving must be one of {SERVING}"))
        if fam.get("swap") not in SWAPS + (None,):
            out.append(Finding("error", "schema", f"families.{fid}: swap must be one of {SWAPS}"))
        hit = next((p for p in prefixes if str(fid).startswith(p)), None)
        if hit:
            by_prefix.setdefault(hit, []).append((fid, fam.get("blob")))
    for prefix, members in by_prefix.items():
        blobs = {blob for _, blob in members}
        if len(blobs) > 1:
            names = ", ".join(f"{fid}={blob}" for fid, blob in members)
            out.append(Finding(
                "error", "shared-prefix",
                f"families share governor prefix {prefix!r} with different blobs ({names}); a new "
                "model may not hide under an old name (design §4)"))


def _lint_ids(reg, prefixes, out):
    for mid in model_ids(reg):
        if not any(str(mid).startswith(p) for p in prefixes):
            out.append(Finding(
                "error", "governor-prefix",
                f"model id {mid!r} does not start with a governor prefix {list(prefixes)}: the "
                "governor would bill it to Anthropic as a cloud model (governor/governor_core.py:97)"))
        if "@" in str(mid):
            out.append(Finding(
                "error", "governor-prefix",
                f"model id {mid!r} contains '@', the effort separator (dispatcher/local_capacity.py:51)"))


def _lint_node(name, node, fams, out):
    if not isinstance(node, dict):
        out.append(Finding("error", "schema", f"nodes.{name}: must be a mapping"))
        return
    if node.get("state") not in STATES:
        out.append(Finding("error", "schema", f"nodes.{name}: state must be one of {STATES}"))
    if node.get("power_policy") not in POWER_POLICIES:
        out.append(Finding("error", "schema", f"nodes.{name}: power_policy must be one of {POWER_POLICIES}"))
    for key in ("tailscale_ip", "plug_id"):
        if not node.get(key):
            out.append(Finding("error", "schema", f"nodes.{name}: missing {key}"))
    models = node.get("models")
    if not isinstance(models, list):
        out.append(Finding("error", "schema", f"nodes.{name}: models must be a list"))
        return
    for i, m in enumerate(models):
        where = f"nodes.{name}.models[{i}]"
        if not isinstance(m, dict):
            out.append(Finding("error", "schema", f"{where}: must be a mapping"))
            continue
        if m.get("family") not in fams:
            out.append(Finding("error", "schema", f"{where}: family {m.get('family')!r} not in families"))
        for key in ("slots", "served_ctx", "first_frame_s"):
            if not _num_ok(m.get(key)):
                out.append(Finding("error", "schema", f"{where}: {key} must be a positive number"))
        for key in ("prefill_tps", "decode_tps", "load_s"):
            if m.get(key) is not None and not _num_ok(m[key]):
                out.append(Finding("error", "schema", f"{where}: {key} must be null (unmeasured) or > 0"))
        for cap, c in (m.get("capabilities") or {}).items():
            if not isinstance(c, dict) or not isinstance(c.get("trusted"), bool):
                out.append(Finding("error", "schema", f"{where}.capabilities.{cap}: needs boolean trusted"))
            elif c.get("source") not in ("declared", "measured"):
                out.append(Finding("error", "schema", f"{where}.capabilities.{cap}: source must be declared|measured"))
            elif c["source"] == "declared" and not c.get("ref"):
                out.append(Finding("error", "schema", f"{where}.capabilities.{cap}: declared needs a ref"))
            elif not c["trusted"] and not c.get("reason"):
                out.append(Finding("error", "schema", f"{where}.capabilities.{cap}: untrusted needs a reason"))


def _lint_uniques(nodes, out):
    for field in ("plug_id", "tailscale_ip"):
        seen = {}
        for name, node in nodes.items():
            v = (node or {}).get(field) if isinstance(node, dict) else None
            if v:
                seen.setdefault(str(v), []).append(name)
        for v, names in seen.items():
            if len(names) > 1:
                out.append(Finding("error", "duplicate", f"{field} {v!r} claimed by {', '.join(names)}"))


def _lint_inventory(nodes, inventory, out):
    try:
        plugs = inventory.plugs()
    except OSError as e:
        out.append(Finding("warn", "inventory", f"plug_id not checked, fleet-inv unavailable: {e}"))
        plugs = None
    for name, node in nodes.items():
        if not isinstance(node, dict):
            continue
        if plugs is not None:
            if name not in plugs:
                out.append(Finding("error", "plug-mismatch", f"nodes.{name}: no plug in fleet-inv plugs"))
            elif str(node.get("plug_id")) != plugs[name]:
                out.append(Finding(
                    "error", "plug-mismatch",
                    f"nodes.{name}: plug_id {node.get('plug_id')!r} != fleet-inv {plugs[name]!r}; a "
                    "stale copy cuts the wrong machine's AC"))
        try:
            ip = inventory.tailscale_ip(name)
        except OSError as e:
            out.append(Finding("warn", "inventory", f"nodes.{name}: tailscale_ip not checked: {e}"))
            continue
        if ip is None:
            out.append(Finding("error", "ip-mismatch", f"nodes.{name}: no tailscale_ip in fleet-inv"))
        elif str(node.get("tailscale_ip")) != ip:
            out.append(Finding(
                "error", "ip-mismatch",
                f"nodes.{name}: tailscale_ip {node.get('tailscale_ip')!r} != fleet-inv {ip!r}"))


def _lint_ceiling(nodes, local_ceiling, out):
    if local_ceiling is None:
        return
    total = sum(
        m.get("slots") or 0
        for node in nodes.values() if isinstance(node, dict) and node.get("state") not in NO_SLOT_STATES
        for m in node.get("models") or [] if isinstance(m, dict) and m.get("resident"))
    if total > local_ceiling:
        out.append(Finding(
            "error", "local-ceiling",
            f"resident slots across live nodes sum to {total} > local_ceiling {local_ceiling} "
            "(dispatcher/config.json)"))


def lint(reg, inventory=None, local_ceiling=None, governor=None):
    """Findings for a loaded registry. `inventory` needs plugs() and tailscale_ip(name) (default:
    the fleet-inv CLI); `local_ceiling` / `governor` default to the repo's dispatcher config and
    governor tuple. Errors (level == 'error') are lint failures; warnings are unchecked rules."""
    out = []
    prefixes = tuple(reg.get("governor_prefixes") or ())
    if not prefixes:
        out.append(Finding("error", "schema", "governor_prefixes: must be a non-empty list"))
    actual = governor if governor is not None else governor_prefixes()
    if actual is None:
        out.append(Finding("warn", "governor-prefix", "governor tuple unreadable; prefixes not compared"))
    elif set(actual) != set(prefixes):
        out.append(Finding(
            "error", "governor-prefix",
            f"governor_prefixes {list(prefixes)} != governor/governor_core.py:97 {list(actual)}; the "
            "registry may not add or drop a prefix the governor does not have (design §4)"))
    _lint_families(reg, prefixes, out)
    _lint_ids(reg, prefixes, out)
    nodes = reg.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        out.append(Finding("error", "schema", "nodes: must be a non-empty mapping"))
        return out
    fams = reg.get("families") or {}
    for name, node in nodes.items():
        _lint_node(name, node, fams, out)
    _lint_uniques(nodes, out)
    _lint_inventory(nodes, inventory or FleetInventory(), out)
    _lint_ceiling(nodes, local_ceiling if local_ceiling is not None else dispatcher_local_ceiling(), out)
    return out


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "lint":
        print(__doc__, file=sys.stderr)
        return 1
    path = args[args.index("--path") + 1] if "--path" in args else None
    try:
        findings = lint(load(path))
    except RegistryError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    for f in findings:
        print(f)
    errors = sum(f.level == "error" for f in findings)
    print(f"inference registry lint: {errors} error(s), {len(findings) - errors} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
