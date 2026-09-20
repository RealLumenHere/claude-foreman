"""node_render_gateway.py — generators for the two gateway-side targets that are not in git.
research/INFERENCE_NODE_REGISTRY.md §3 rows 1 and 4; wq#2251 (C3 of wq#2241).

  render_litellm(reg)        -> the sentinel-delimited model_list block (ops/inference-registry/
                                litellm_local_block.yaml); apply_litellm() splices it into
                                /home/lumen/agent/litellm_config.yaml after a timestamped backup,
                                restart_gateway() bounces the container via a detached systemd-run.
  render_key_allowlist(reg)  -> the registry-owned model names for the scoped `foreman-local-rung`
                                key; apply_key_allowlist() merges them into the key's live list and
                                calls /key/update.

Both renders are pure functions of the registry and both applies are idempotent: an unchanged
target is not backed up, not rewritten and not restarted, so re-running with the pre-change
registry is the rollback (retire's one-liner, design §5). rollback_* restore the newest backup.

Credential rule (instinct 4): keys are read from their 0600 files, held in one local variable,
sent in an Authorization header or JSON body in-process — never on a command line, in a URL, in
a log line or in an exception message (every message goes through _mask).

CLI:  python3 harness/node_render_gateway.py {render|apply|rollback} [--check] [--dry-run] ...
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import inference_registry as ir  # noqa: E402

REPO_ROOT = os.path.join(HERE, "..")
BLOCK_PATH = os.path.join(REPO_ROOT, "ops", "inference-registry", "litellm_local_block.yaml")
LITELLM_CONFIG = os.environ.get("FOREMAN_LITELLM_CONFIG", "/home/lumen/agent/litellm_config.yaml")
LITELLM_BASE_URL = os.environ.get("FOREMAN_LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_CONTAINER = os.environ.get("FOREMAN_LITELLM_CONTAINER", "litellm")
# The scoped key being edited (same file despag/backends/litellm.py:25 reads).
KEY_FILE = os.environ.get("FOREMAN_LITELLM_KEY_FILE", "/home/lumen/.secrets/foreman-litellm.token")
# /key/update needs an admin key; unset means "try the scoped key" and fail with a clear message.
ADMIN_KEY_FILE = os.environ.get("FOREMAN_LITELLM_ADMIN_KEY_FILE")

GROUP = "local-worker"
BEGIN = "# BEGIN GENERATED inference_nodes.yaml v1 — edit the registry, not this block"
END = "# END GENERATED"
NOT_SERVED_STATES = ("retired",)  # a `down` node keeps its deployment: LiteLLM cooldown handles it
TIMEOUT_MARGIN_S = 60             # first_frame_s + 60: LiteLLM's disconnect watcher stops at 600 s
OUTPUT_HEADROOM = 4096            # max_output_tokens; max_input_tokens = served_ctx - this
COOLDOWN_S = 900
_PLAIN = re.compile(r"^[A-Za-z0-9_./:@-]+$")


class GatewayError(Exception):
    pass


def _mask(text, *secrets):
    text = str(text)
    for s in secrets:
        if s:
            text = text.replace(s, "<masked>")
    return text


def _scalar(v):
    return v if isinstance(v, str) and _PLAIN.match(v) else json.dumps(v)


def _served(reg):
    """(node, model, family) for every deployment the gateway should carry, registry order."""
    fams = reg.get("families") or {}
    for node_name, node in (reg.get("nodes") or {}).items():
        if not isinstance(node, dict) or node.get("state") in NOT_SERVED_STATES:
            continue
        for m in node.get("models") or []:
            yield node_name, node, m, fams[m["family"]]


def owned_names(reg):
    """Every model_name the generated block may define, whether or not a node serves it now."""
    return set(reg.get("families") or {}) | {GROUP}


def _entry(node_name, node, m, fam, group):
    timeout = int(m["first_frame_s"]) + TIMEOUT_MARGIN_S
    gw_model = fam.get("gateway_model") or fam["blob"]
    desc = f"{fam['blob']} on {node_name} ({fam['serving']}), {m['slots']} slots x {m['served_ctx']} served"
    lines = [
        f"- model_name: {GROUP if group else m['family']}",
        "  litellm_params:",
        f"    model: {_scalar(f'openai/{gw_model}')}",
        f"    api_base: http://{node['tailscale_ip']}:{fam['port']}/v1",
        f"    api_key: {'none' if fam['serving'] == 'ollama' else 'sk-none'}",
        f"    timeout: {timeout}",
        f"    stream_timeout: {timeout}",
        "    num_retries: 0",
    ]
    if fam.get("extra_body"):
        lines.append(f"    extra_body: {json.dumps(fam['extra_body'])}")
    lines.append(f"    max_parallel_requests: {m['slots']}")
    info = {"mode": "chat"}
    if group:
        lines.append(f"    cooldown_time: {COOLDOWN_S}")
        info.update(id=f"{GROUP}-{node_name}", max_input_tokens=int(m["served_ctx"]) - OUTPUT_HEADROOM,
                    max_output_tokens=OUTPUT_HEADROOM)
    info["description"] = desc
    lines.append("  model_info: {" + ", ".join(f"{k}: {_scalar(v)}" for k, v in info.items()) + "}")
    return lines


def render_litellm(reg):
    """The sentinel-wrapped model_list items: one pin per served model, then the local-worker group."""
    served = list(_served(reg))
    lines = [BEGIN]
    for group in (False, True):
        for node_name, node, m, fam in served:
            lines += _entry(node_name, node, m, fam, group)
    lines.append(END)
    return "\n".join(lines) + "\n"


def render_key_allowlist(reg):
    """Registry-owned model names the scoped key must carry: the group plus each served pin."""
    pins = list(dict.fromkeys(m["family"] for _, _, m, _ in _served(reg)))
    return ([GROUP] if pins else []) + pins


def merge_allowlist(current, reg):
    """The key's live models with every registry-owned name replaced by the rendered set, so
    non-registry entries (deepseek-v4-flash) survive and a retired node's names drop."""
    owned = owned_names(reg)
    return [m for m in current if m not in owned] + render_key_allowlist(reg)


# --- litellm_config.yaml splice -------------------------------------------------------------

def _model_list_chunks(lines):
    """(start, end, model_name) per model_list item; end is exclusive, and the last item stops at
    the next top-level key."""
    head = next((i for i, ln in enumerate(lines) if re.match(r"model_list:\s*(#.*)?$", ln)), None)
    if head is None:
        raise GatewayError("litellm config has no top-level model_list:")
    stop = len(lines)
    for i in range(head + 1, len(lines)):
        if re.match(r"[A-Za-z_]", lines[i]):
            stop = i
            break
    starts = [i for i in range(head + 1, stop) if lines[i].startswith("- ")]
    chunks = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else stop
        name = re.match(r"- model_name:\s*(\S+)", lines[s])
        chunks.append((s, e, name.group(1) if name else None))
    return chunks, stop


def splice_block(text, block, owned):
    """`text` with the generated block installed. With sentinels present, only that region is
    replaced. Without, the first run adopts: hand-written items whose model_name the block owns
    are removed and the block takes the first one's place. Refuses when an owned name is also
    defined outside the block (a duplicate deployment would silently double-route)."""
    lines = text.splitlines()
    new_block = block.splitlines()
    begins = [i for i, ln in enumerate(lines) if ln.strip() == BEGIN]
    ends = [i for i, ln in enumerate(lines) if ln.strip() == END]
    if begins or ends:
        if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
            raise GatewayError("litellm config has unbalanced GENERATED sentinels; not touching it")
        b, e = begins[0], ends[0]
        outside = lines[:b] + lines[e + 1:]
        dup = sorted(n for _, _, n in _model_list_chunks(outside)[0] if n in owned)
        if dup:
            raise GatewayError(f"registry-owned model_name(s) also defined outside the block: {dup}")
        out = lines[:b] + new_block + lines[e + 1:]
    else:
        chunks, stop = _model_list_chunks(lines)
        drop = [(s, e) for s, e, n in chunks if n in owned]
        at = drop[0][0] if drop else stop
        keep = [ln for i, ln in enumerate(lines) if not any(s <= i < e for s, e in drop)]
        out = keep[:at] + new_block + keep[at:]  # every dropped item starts at or after `at`
    return "\n".join(out) + "\n"


def _timestamp(dir_, stem):
    base = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    ts, n = base, 0
    while glob.glob(os.path.join(dir_, f"{stem}*{ts}*")):
        n += 1
        ts = f"{base}-{n}"
    return ts


def _atomic_write(path, text):
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w") as f:
        f.write(text)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)


def apply_litellm(reg, config_path=None):
    """Splice the block into the gateway config. -> {changed, backup}. Backup precedes the write;
    an unchanged config is neither backed up nor rewritten."""
    path = config_path or LITELLM_CONFIG
    try:
        with open(path) as f:
            old = f.read()
    except OSError as e:
        raise GatewayError(f"cannot read {path}: {e}") from e
    new = splice_block(old, render_litellm(reg), owned_names(reg))
    if new == old:
        return {"changed": False, "backup": None}
    try:
        import yaml
        parsed = yaml.safe_load(new)
        if not isinstance(parsed.get("model_list"), list):
            raise ValueError("model_list is not a list")
    except Exception as e:  # noqa: BLE001 — refuse to write a config LiteLLM could not load
        raise GatewayError(f"spliced config does not parse, nothing written: {e}") from e
    backup = f"{path}.bak-{_timestamp(os.path.dirname(path) or '.', os.path.basename(path))}"
    shutil.copy2(path, backup)
    _atomic_write(path, new)
    return {"changed": True, "backup": backup}


def newest_backup(path):
    found = sorted(glob.glob(f"{path}.bak-*"))
    return found[-1] if found else None


def rollback_litellm(config_path=None):
    """Restore the newest backup over the config (the current file is itself backed up first)."""
    path = config_path or LITELLM_CONFIG
    src = newest_backup(path)
    if not src:
        raise GatewayError(f"no {os.path.basename(path)}.bak-* to roll back to")
    keep = f"{path}.pre-rollback-{_timestamp(os.path.dirname(path) or '.', os.path.basename(path))}"
    shutil.copy2(path, keep)
    with open(src) as f:
        _atomic_write(path, f.read())
    return {"restored": src, "saved_current": keep}


def restart_gateway(container=None, runner=subprocess.run, stamp=None):
    """Bounce the gateway container as a detached transient unit — never in the foreground: this
    session may itself be riding the gateway (instinct 5). Returns the unit name."""
    unit = f"fleet-node-gateway-restart-{stamp or int(time.time())}"
    cmd = ["sudo", "-n", "systemd-run", f"--unit={unit}", "--collect", "--no-block",
           "docker", "restart", container or LITELLM_CONTAINER]
    p = runner(cmd, capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise GatewayError(f"systemd-run refused ({p.returncode}): {p.stderr.strip()[:200]}")
    return unit


# --- scoped key allowlist -------------------------------------------------------------------

def _read_secret(path):
    try:
        with open(path) as f:
            secret = f.read().strip()
    except OSError as e:
        raise GatewayError(f"cannot read key file {path}: {e.strerror}") from e
    if not secret:
        raise GatewayError(f"key file {path} is empty")
    return secret


def _http(method, url, headers, body=None, timeout=20):
    """-> (status, parsed json or None). Error text never carries the request headers."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise GatewayError(f"gateway unreachable: {type(e).__name__}") from e


def _key_state(http, base, scoped):
    """The scoped key's live models list, read with the key's own Authorization header (an
    absent `key` param means "the caller's key"), so the secret never enters a URL."""
    status, body = http("GET", f"{base}/key/info", {"Authorization": f"Bearer {scoped}"})
    if status != 200 or not isinstance(body, dict):
        raise GatewayError(f"/key/info answered {status} for the scoped key")
    models = (body.get("info") or {}).get("models")
    if not isinstance(models, list):
        raise GatewayError("/key/info returned no models list")
    return models


def apply_key_allowlist(reg, key_file=None, admin_key_file=None, base_url=None, http=_http,
                        backup_dir=None):
    """Merge the registry-owned names into the scoped key's live allowlist and /key/update it.
    -> {changed, before, after, backup}. The prior list is saved (model names only) before the call."""
    base = (base_url or LITELLM_BASE_URL).rstrip("/")
    scoped = _read_secret(key_file or KEY_FILE)
    admin_path = admin_key_file or ADMIN_KEY_FILE
    admin = _read_secret(admin_path) if admin_path else scoped
    secrets = (scoped, admin)
    try:
        before = _key_state(http, base, scoped)
        after = merge_allowlist(before, reg)
        if after == before:
            return {"changed": False, "before": before, "after": after, "backup": None}
        backup = _save_key_models(before, backup_dir)
        status, _ = http("POST", f"{base}/key/update",
                         {"Authorization": f"Bearer {admin}", "Content-Type": "application/json"},
                         {"key": scoped, "models": after})
    except GatewayError as e:
        raise GatewayError(_mask(e, *secrets)) from None
    if status in (401, 403):
        raise GatewayError(f"/key/update refused ({status}); set FOREMAN_LITELLM_ADMIN_KEY_FILE to "
                           "a file holding an admin key")
    if status != 200:
        raise GatewayError(f"/key/update answered {status}")
    return {"changed": True, "before": before, "after": after, "backup": backup}


def _save_key_models(models, backup_dir):
    d = backup_dir or os.path.dirname(LITELLM_CONFIG)
    stem = "foreman-local-rung.models"
    path = os.path.join(d, f"{stem}.bak-{_timestamp(d, stem)}.json")
    with open(path, "w") as f:
        json.dump(models, f)
    return path


def rollback_key_allowlist(key_file=None, admin_key_file=None, base_url=None, http=_http,
                           backup_dir=None):
    """Put the newest saved pre-change models list back on the key."""
    d = backup_dir or os.path.dirname(LITELLM_CONFIG)
    found = sorted(glob.glob(os.path.join(d, "foreman-local-rung.models.bak-*.json")))
    if not found:
        raise GatewayError("no saved foreman-local-rung models list to roll back to")
    with open(found[-1]) as f:
        models = json.load(f)
    base = (base_url or LITELLM_BASE_URL).rstrip("/")
    scoped = _read_secret(key_file or KEY_FILE)
    admin_path = admin_key_file or ADMIN_KEY_FILE
    admin = _read_secret(admin_path) if admin_path else scoped
    try:
        status, _ = http("POST", f"{base}/key/update",
                         {"Authorization": f"Bearer {admin}", "Content-Type": "application/json"},
                         {"key": scoped, "models": models})
    except GatewayError as e:
        raise GatewayError(_mask(e, scoped, admin)) from None
    if status != 200:
        raise GatewayError(f"/key/update answered {status}")
    return {"restored": found[-1], "models": models}


# --- CLI ------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("render", "apply", "rollback"))
    ap.add_argument("--registry")
    ap.add_argument("--check", action="store_true", help="render: exit 2 if the committed block drifted")
    ap.add_argument("--dry-run", action="store_true", help="apply: show the change, touch nothing")
    ap.add_argument("--no-restart", action="store_true")
    ap.add_argument("--force-restart", action="store_true", help="restart even when the config was unchanged")
    ap.add_argument("--block-path", default=BLOCK_PATH)
    ap.add_argument("--config", default=LITELLM_CONFIG)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "rollback":
            print(json.dumps(rollback_litellm(args.config)))
            print(json.dumps({k: v for k, v in rollback_key_allowlist().items() if k != "models"}))
            print("restart the gateway to load the restored config", file=sys.stderr)
            return 0
        reg = ir.load(args.registry)
        block, allow = render_litellm(reg), render_key_allowlist(reg)
        if args.cmd == "render":
            cur = open(args.block_path).read() if os.path.exists(args.block_path) else None
            if args.check:
                if cur != block:
                    print(f"DRIFT {args.block_path} differs from the registry render", file=sys.stderr)
                    return 2
            else:
                os.makedirs(os.path.dirname(args.block_path), exist_ok=True)
                with open(args.block_path, "w") as f:
                    f.write(block)
            print(json.dumps({"models": allow}))
            return 0
        if args.dry_run:
            new = splice_block(open(args.config).read(), block, owned_names(reg))
            print(json.dumps({"config_changes": new != open(args.config).read(), "key_models_owned": allow}))
            return 0
        res = apply_litellm(reg, args.config)
        print(json.dumps({"litellm": res}))
        if (res["changed"] or args.force_restart) and not args.no_restart:
            print(json.dumps({"restart_unit": restart_gateway()}))
        key = apply_key_allowlist(reg)
        print(json.dumps({"key": {k: key[k] for k in ("changed", "after", "backup")}}))
        return 0
    except (GatewayError, ir.RegistryError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
