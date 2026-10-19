#!/usr/bin/env python3
"""wq#4181 — validate and render the config-only role files for the TF1
appliance roles (tf1-a exit node, tf1-b external vantage, tf1-c ADB/RE
host, tf1-d Limpet testbed — the last added by wq#4319). See roles.yaml
and README.md.

Sibling of ops/wq3430-spectrio-tf1-deploy/render_role.py (the earlier
5-role plan); kept separate because the role kinds and required fields
don't overlap. Validation here is syntax/schema only — no fleet-exec grant
for these units yet, so it cannot and does not claim to prove a role boots
on real hardware.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROLES_FILE = Path(__file__).parent / "roles.yaml"

REQUIRED_COMMON = ("kind", "name", "hostname", "tailnet", "services")

REQUIRED_BY_KIND = {
    "exit_node": ("advertise_routes", "cpu_governor", "sysctl_dropin"),
    "external_vantage": ("gatus_image", "ntfy_bind", "ntfy_acl"),
    "adb_host": ("adb_bind", "adb_mode", "firewall", "re_tools"),
    "limpet_testbed": (
        "hal_daemon_service",
        "registry_daemon_service",
        "meta_server_enabled",
    ),
}

RE_TOOLS_REQUIRED = frozenset(
    {"rkdeveloptool", "mtkclient", "pyamlboot", "heimdall", "flashrom"}
)


class RoleValidationError(ValueError):
    pass


def load_roles(path: Path = ROLES_FILE) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def validate_role(role_id: str, role: dict) -> None:
    missing_common = [f for f in REQUIRED_COMMON if f not in role]
    if missing_common:
        raise RoleValidationError(
            f"{role_id}: missing common field(s) {missing_common}"
        )

    kind = role["kind"]
    if kind not in REQUIRED_BY_KIND:
        raise RoleValidationError(f"{role_id}: unknown kind {kind!r}")

    missing_kind = [f for f in REQUIRED_BY_KIND[kind] if f not in role]
    if missing_kind:
        raise RoleValidationError(
            f"{role_id}: kind {kind!r} missing required field(s) {missing_kind}"
        )

    if kind == "exit_node" and role["cpu_governor"] != "performance":
        raise RoleValidationError(
            f"{role_id}: exit_node roles must run cpu_governor: performance"
        )

    if kind == "external_vantage" and role["ntfy_acl"] != "deny-all":
        raise RoleValidationError(
            f"{role_id}: external_vantage roles must ship ntfy_acl: deny-all "
            "(no anonymous read/write until a real credential is provisioned)"
        )

    if kind == "adb_host" and role["adb_mode"] != "nodaemon":
        raise RoleValidationError(
            f"{role_id}: adb_host roles must run adb in nodaemon mode "
            "(systemd needs a foreground process, not a self-forking daemon)"
        )

    if kind == "adb_host" and not RE_TOOLS_REQUIRED.issubset(set(role["re_tools"])):
        raise RoleValidationError(
            f"{role_id}: adb_host roles must list every RE tool "
            f"{sorted(RE_TOOLS_REQUIRED)}, got {role['re_tools']!r}"
        )

    if kind == "limpet_testbed" and role["meta_server_enabled"] is not False:
        raise RoleValidationError(
            f"{role_id}: limpet_testbed roles must ship meta_server_enabled: "
            "false (wq#3808 MCP meta-server is under the wq#3786 REA security "
            "hold — this role may not flip it on)"
        )

    if role["tailnet"] is not True:
        raise RoleValidationError(
            f"{role_id}: every tf1-a/b/c/d role is tailnet-reachable (tailnet: true)"
        )


def validate_all(data: dict) -> None:
    roles = data["roles"]
    for role_id, role in roles.items():
        validate_role(role_id, role)

    build_order = data["build_order"]
    if set(build_order) != set(roles):
        raise RoleValidationError(
            f"build_order {build_order} does not match role set {sorted(roles)}"
        )


def render_role(role_id: str, role: dict) -> str:
    lines = [
        f"# first-boot config for {role_id} ({role['name']})",
        f"hostnamectl set-hostname {role['hostname']}",
    ]
    for svc in role["services"]:
        lines.append(f"systemctl enable --now {svc}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    data = load_roles()

    if argv[:1] == ["--validate"]:
        validate_all(data)
        print(f"OK: {len(data['roles'])} roles valid")
        return 0

    if not argv:
        print("usage: render_role.py <role-id> | --validate", file=sys.stderr)
        return 2

    role_id = argv[0]
    roles = data["roles"]
    if role_id not in roles:
        print(f"unknown role {role_id!r}; known: {sorted(roles)}", file=sys.stderr)
        return 2

    validate_role(role_id, roles[role_id])
    print(render_role(role_id, roles[role_id]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
