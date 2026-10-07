#!/usr/bin/env python3
"""wq#3430 — validate and render the config-only role files for the 5 TF1
single-purpose appliances (see roles.yaml and README.md).

All 5 units share one base image (ophub Armbian S905X3, proven by wq#3429);
a role is nothing but this file applied on first boot. Validation here is
syntax/schema only — it has no hardware to test against (no fleet-exec grant
for these units yet), so it cannot and does not claim to prove a role boots.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROLES_FILE = Path(__file__).parent / "roles.yaml"

REQUIRED_COMMON = ("kind", "name", "hostname", "tailnet", "services")

REQUIRED_BY_KIND = {
    "klipper_host": ("serial_devices_min",),
    "dns_resolver": ("static_ip", "failover_peer"),
    "kiosk": ("target_url", "display"),
    "spare": ("proves_flash_procedure",),
}


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

    if kind == "klipper_host" and role["serial_devices_min"] < 2:
        raise RoleValidationError(
            f"{role_id}: serial_devices_min must be >= 2 (ticket requires >=2 USB serial)"
        )

    if kind == "dns_resolver" and role["tailnet"] is not False:
        raise RoleValidationError(
            f"{role_id}: dns_resolver roles must have tailnet: false (static IPs, no tailnet)"
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
    if build_order[0] != "pathfinder-spare":
        raise RoleValidationError(
            "pathfinder-spare must be first in build_order — it proves the "
            "flash procedure before any other unit is built"
        )


def render_role(role_id: str, role: dict) -> str:
    lines = [
        f"# first-boot config for {role_id} ({role['name']})",
        f"hostnamectl set-hostname {role['hostname']}",
    ]
    static_ip = role.get("static_ip")
    if static_ip and not str(static_ip).startswith("TBD"):
        lines.append(f"# static IP: {static_ip} (tailnet={role['tailnet']})")
    for svc in role["services"]:
        lines.append(f"systemctl enable --now {svc}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    data = load_roles()

    if argv[:1] == ["--validate"]:
        validate_all(data)
        print(f"OK: {len(data['roles'])} roles valid, build_order starts with pathfinder-spare")
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
