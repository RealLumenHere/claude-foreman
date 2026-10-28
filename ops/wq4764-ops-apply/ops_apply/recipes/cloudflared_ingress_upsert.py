"""cloudflared.ingress.upsert v1 (design §3.3). Upserts or removes exactly
one ingress rule in a node's /etc/cloudflared/config.yml, validated by YAML
structure rather than text diff, with cloudflared's own `ingress validate`/
`ingress rule` commands as the ground truth, a TOCTOU recheck, atomic
rename, restart, post-check, auto-rollback and an inverse manifest.

Every step that touches the filesystem or execs a process takes its I/O as
an injected callable (`runner`, `clock`, `http_get`), so the full apply/
rollback/post-check state machine is unit-testable with fakes — no real
cloudflared binary, systemd or network needed for B2-T1 (code+tests, no
install, no live node).
"""
import os
import re
import subprocess
import time

import yaml

from ..canonical import file_sha256
from ..errors import OpsApplyError
from ..validation import check_toctou

RECIPE_ID = "cloudflared.ingress.upsert"
RECIPE_VERSION = 1
UNIT = "cloudflared"
TARGET_PATH = "/etc/cloudflared/config.yml"

_PATH_RE = r"^/[A-Za-z0-9/_.-]{0,64}$"
_LABEL = r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"


def build_param_schema(zones):
    """zones: the node catalog entry's list of zones a hostname may sit
    under (design: "must sit under a zone listed in the node's catalog
    entry"). Baking the zone list into the regex keeps V5 a plain JSON
    Schema check with additionalProperties:false — no special-cased zone
    lookup outside the schema."""
    zone_alt = "|".join(re.escape(z) for z in zones)
    hostname_pattern = rf"^{_LABEL}(\.{_LABEL})*\.({zone_alt})$"
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "hostname", "port"],
        "properties": {
            "action": {"enum": ["upsert", "remove"]},
            "hostname": {"type": "string", "pattern": hostname_pattern, "maxLength": 253},
            "port": {"type": "integer", "minimum": 1024, "maximum": 65535},
            "path": {"type": "string", "pattern": _PATH_RE},
        },
    }


class RecipeError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


CATCHALL = {"service": "http_status:404"}


def _rule_key(rule):
    return (rule.get("hostname"), rule.get("path"))


def apply_ingress_op(ingress, params):
    """Step 2: apply the op to ingress[] in memory. Returns (new_ingress,
    inverse_params) — inverse_params is what undoes this exact change
    (design §3.3 "Inverse")."""
    action = params["action"]
    hostname = params["hostname"]
    path = params.get("path")
    port = params.get("port")
    service = f"http://127.0.0.1:{port}" if port is not None else None
    key = (hostname, path)

    body = [r for r in ingress if r != CATCHALL]
    existing = next((r for r in body if _rule_key(r) == key), None)

    if action == "remove":
        if existing is None:
            raise RecipeError("remove_target_missing")
        new_body = [r for r in body if _rule_key(r) != key]
        inverse = {"action": "upsert", "hostname": hostname, "port": _port_of(existing)}
        if path:
            inverse["path"] = path
        return new_body, inverse

    # upsert
    new_rule = {"hostname": hostname, "service": service}
    if path:
        new_rule["path"] = path
    if existing is None:
        new_body = body + [new_rule]
        inverse = {"action": "remove", "hostname": hostname, "port": port}
        if path:
            inverse["path"] = path
        return new_body, inverse
    new_body = [new_rule if _rule_key(r) == key else r for r in body]
    inverse = {"action": "upsert", "hostname": hostname, "port": _port_of(existing)}
    if path:
        inverse["path"] = path
    return new_body, inverse


def _port_of(rule):
    m = re.match(r"http://127\.0\.0\.1:(\d+)$", rule.get("service", ""))
    return int(m.group(1)) if m else None


def validate_diff(old_doc, new_doc):
    """Step 3: YAML-structural diff validation (design §3.3 step 3). Raises
    RecipeError; never compares as text."""
    for key in set(old_doc) | set(new_doc):
        if key == "ingress":
            continue
        if old_doc.get(key) != new_doc.get(key):
            raise RecipeError(f"other_key_changed:{key}")

    old_ingress = old_doc.get("ingress") or []
    new_ingress = new_doc.get("ingress") or []
    if not new_ingress or new_ingress[-1] != CATCHALL:
        raise RecipeError("catchall_missing_or_moved")
    if any(r == CATCHALL for r in new_ingress[:-1]):
        raise RecipeError("catchall_duplicated")

    old_body = old_ingress[:-1] if old_ingress[-1:] == [CATCHALL] else old_ingress
    new_body = new_ingress[:-1]

    new_keys = [_rule_key(r) for r in new_body]
    if len(set(new_keys)) != len(new_keys):
        raise RecipeError("duplicate_host_path")

    changed = _count_changed_rules(old_body, new_body)
    if changed != 1:
        raise RecipeError(f"expected_exactly_one_rule_changed:{changed}")

    for r in new_body:
        if _rule_key(r) not in [_rule_key(o) for o in old_body] or r not in old_body:
            service = r.get("service", "")
            if not re.match(r"^http://127\.0\.0\.1:\d+$", service):
                raise RecipeError(f"non_loopback_service:{service!r}")


def _count_changed_rules(old_body, new_body):
    old_by_key = {_rule_key(r): r for r in old_body}
    new_by_key = {_rule_key(r): r for r in new_body}
    changed = 0
    for key in set(old_by_key) | set(new_by_key):
        if old_by_key.get(key) != new_by_key.get(key):
            changed += 1
    return changed


def render_argv(candidate_path, hostname, path):
    """Step 4's two fixed argv lists, also handed to V9 for the deny-list
    recheck before anything execs."""
    url = f"https://{hostname}{path or ''}"
    return [
        ["cloudflared", "tunnel", "--config", candidate_path, "ingress", "validate"],
        ["cloudflared", "tunnel", "--config", candidate_path, "ingress", "rule", url],
    ]


class ApplyResult:
    def __init__(self, status, detail=None, backup_path=None, inverse_manifest=None):
        self.status = status
        self.detail = detail
        self.backup_path = backup_path
        self.inverse_manifest = inverse_manifest


def apply(*, manifest, work_dir, backup_root, req_id, runner=subprocess.run,
          clock=time.time, http_get=None, config_path=TARGET_PATH,
          post_check_timeout=60):
    """Runs steps 1-10 of §3.3 end to end. `runner` execs argv lists
    (candidate validate/rule, systemctl restart, systemctl is-active);
    `http_get(url)` -> int status code, stubbed by the caller for a non-
    network unit test. Returns an ApplyResult; never raises for a post-check
    failure (that is ROLLED_BACK/ROLLBACK_FAILED, not an exception) but does
    raise RecipeError/OpsApplyError for a refusal before anything is
    staged.
    """
    params = manifest["params"]

    # Step 1: read + sha
    with open(config_path) as f:
        old_text = f.read()
    old_doc = yaml.safe_load(old_text)
    live_sha_at_read = file_sha256(config_path)

    # Step 2: apply the op in memory
    old_ingress = old_doc.get("ingress") or []
    new_body, inverse_params = apply_ingress_op(old_ingress, params)
    new_doc = dict(old_doc)
    new_doc["ingress"] = new_body + [CATCHALL]

    # Step 3: structural diff validation
    validate_diff(old_doc, new_doc)

    # Step 4: write candidate, cloudflared validate + rule
    os.makedirs(work_dir, exist_ok=True)
    candidate_path = os.path.join(work_dir, f"{req_id}.candidate.yml")
    with open(candidate_path, "w") as f:
        yaml.safe_dump(new_doc, f, sort_keys=False)
    for argv in render_argv(candidate_path, params["hostname"], params.get("path")):
        result = runner(argv, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RecipeError(f"cloudflared_check_failed:{' '.join(argv)}")

    # Step 5: backup
    backup_dir = os.path.join(backup_root, req_id)
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, os.path.basename(config_path))
    st = os.stat(config_path)
    with open(config_path, "rb") as src, open(backup_path, "wb") as dst:
        dst.write(src.read())
    os.chmod(backup_path, st.st_mode & 0o777)
    backup_sha = file_sha256(backup_path)

    # Step 6: TOCTOU recheck
    check_toctou(file_sha256(config_path), live_sha_at_read)
    if file_sha256(backup_path) != backup_sha:  # pragma: no cover - defensive
        raise OpsApplyError("OPS_TOCTOU", "backup sha changed after write")

    # Step 7: atomic rename, preserving owner/mode
    os.chmod(candidate_path, st.st_mode & 0o777)
    os.replace(candidate_path, config_path)

    # Step 8: restart
    restart = runner(["systemctl", "restart", UNIT], capture_output=True, text=True, check=False)
    if restart.returncode != 0:
        return _rollback(config_path, backup_path, runner, clock, http_get,
                          params, inverse_params, req_id, backup_dir,
                          reason="restart_failed")

    # Step 9: post-check within post_check_timeout
    ok, reason = _post_check(config_path, params, runner, clock, http_get,
                              deadline=clock() + post_check_timeout)
    if ok:
        return ApplyResult("APPLIED", backup_path=backup_path,
                            inverse_manifest=_inverse_manifest(manifest, inverse_params))

    # Step 10: on post-check failure, roll back
    return _rollback(config_path, backup_path, runner, clock, http_get,
                      params, inverse_params, req_id, backup_dir, reason=reason)


def _post_check(config_path, params, runner, clock, http_get, *, deadline):
    if clock() > deadline:
        return False, "post_check_timeout"
    active = runner(["systemctl", "is-active", UNIT], capture_output=True, text=True, check=False)
    if active.returncode != 0 or active.stdout.strip() != "active":
        return False, "unit_not_active"
    if clock() > deadline:
        return False, "post_check_timeout"
    url = f"https://{params['hostname']}{params.get('path') or ''}"
    rule = runner(["cloudflared", "tunnel", "--config", config_path, "ingress", "rule", url],
                   capture_output=True, text=True, check=False)
    if rule.returncode != 0:
        return False, "ingress_rule_mismatch"
    if clock() > deadline:
        return False, "post_check_timeout"
    if http_get is not None:
        status = http_get(url)
        expected_bad = {1033, 502}
        if params["action"] == "remove":
            if status != 404:
                return False, f"expected_404_got_{status}"
        elif status in expected_bad:
            return False, f"cloudflare_error_{status}"
    return True, None


def _rollback(config_path, backup_path, runner, clock, http_get, params,
              inverse_params, req_id, backup_dir, *, reason):
    """Step 10: restore the backup (copied, not moved — the backup under
    backup_dir is retained for audit/`ops-apply revert`), restart, and
    re-run the post-check against the OLD expectations."""
    if not os.path.exists(backup_path):
        raise OpsApplyError("OPS_TOCTOU", "backup missing during rollback")
    with open(backup_path, "rb") as src, open(config_path, "wb") as dst:
        dst.write(src.read())
    restart = runner(["systemctl", "restart", UNIT], capture_output=True, text=True, check=False)
    if restart.returncode != 0:
        return ApplyResult("ROLLBACK_FAILED", detail=f"{reason}; restart_failed_on_rollback")
    ok, rb_reason = _post_check(config_path, _old_expectation_params(params, inverse_params),
                                 runner, clock, http_get, deadline=clock() + 60)
    if not ok:
        return ApplyResult("ROLLBACK_FAILED", detail=f"{reason}; rollback_postcheck_failed:{rb_reason}")
    return ApplyResult("ROLLED_BACK", detail=reason)


def _old_expectation_params(params, inverse_params):
    """What the post-check should expect once the OLD file is restored:
    inverting the attempted action tells us whether the live rule should
    now be absent (attempted an upsert of a brand-new host) or present with
    its old service (attempted an upsert of an existing host, or a
    remove)."""
    if inverse_params["action"] == "remove":
        return {"action": "remove", "hostname": params["hostname"], "path": params.get("path")}
    return {"action": "upsert", "hostname": params["hostname"], "path": params.get("path")}


def _inverse_manifest(manifest, inverse_params):
    inv = dict(manifest)
    inv["params"] = inverse_params
    inv.pop("digest", None)
    return inv
