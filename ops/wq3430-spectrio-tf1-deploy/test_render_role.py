from pathlib import Path

import pytest

from render_role import (
    RoleValidationError,
    load_roles,
    render_role,
    validate_all,
    validate_role,
)

DATA = load_roles(Path(__file__).parent / "roles.yaml")
EXPECTED_ROLES = {
    "pathfinder-spare",
    "klipper-host",
    "adguard-primary",
    "adguard-secondary",
    "kiosk-dashboard",
}


def test_exactly_five_single_purpose_roles():
    assert set(DATA["roles"]) == EXPECTED_ROLES


def test_all_roles_pass_validation():
    validate_all(DATA)  # raises on any failure


def test_pathfinder_is_built_first():
    assert DATA["build_order"][0] == "pathfinder-spare"


def test_klipper_host_requires_two_serial_devices():
    role = DATA["roles"]["klipper-host"]
    assert role["serial_devices_min"] >= 2


def test_adguard_pair_is_tailnet_free_with_failover():
    primary = DATA["roles"]["adguard-primary"]
    secondary = DATA["roles"]["adguard-secondary"]
    assert primary["tailnet"] is False
    assert secondary["tailnet"] is False
    assert primary["failover_peer"] == "adguard-secondary"
    assert secondary["failover_peer"] == "adguard-primary"


def test_kiosk_targets_a_dashboard_over_hdmi():
    role = DATA["roles"]["kiosk-dashboard"]
    assert "HDMI" in role["display"]
    assert role["target_url"]


def test_render_includes_hostname_and_services():
    role = DATA["roles"]["klipper-host"]
    out = render_role("klipper-host", role)
    assert "tf1-klipper" in out
    assert "klipper.service" in out


@pytest.mark.parametrize(
    "bad_role",
    [
        {"kind": "dns_resolver", "name": "x", "hostname": "h", "tailnet": False, "services": []},
        {
            "kind": "dns_resolver",
            "name": "x",
            "hostname": "h",
            "tailnet": True,
            "services": [],
            "static_ip": "1.2.3.4",
            "failover_peer": "y",
        },
        {
            "kind": "klipper_host",
            "name": "x",
            "hostname": "h",
            "tailnet": True,
            "services": [],
            "serial_devices_min": 1,
        },
    ],
)
def test_invalid_roles_are_rejected(bad_role):
    with pytest.raises(RoleValidationError):
        validate_role("bad-role", bad_role)
