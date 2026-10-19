import subprocess
from pathlib import Path

import pytest

from render_role import (
    RoleValidationError,
    load_roles,
    render_role,
    validate_all,
    validate_role,
)

ROOT = Path(__file__).parent
DATA = load_roles(ROOT / "roles.yaml")
EXPECTED_ROLES = {
    "tf1-a-exit-node",
    "tf1-b-external-vantage",
    "tf1-c-adb-host",
    "tf1-d-limpet-testbed",
}

SHELL_SCRIPTS = [
    ROOT / "harden-armbian.sh",
    ROOT / "tf1-a-exit-node.sh",
    ROOT / "tf1-c-adb-host.sh",
    ROOT / "tf1-d-limpet-testbed.sh",
    ROOT / "tf1-b-external-vantage" / "launch-gatus.sh",
    ROOT / "tf1-b-external-vantage" / "launch-ntfy.sh",
]


def test_exactly_four_roles():
    assert set(DATA["roles"]) == EXPECTED_ROLES


def test_all_roles_pass_validation():
    validate_all(DATA)  # raises on any failure


def test_every_role_is_tailnet_reachable():
    for role in DATA["roles"].values():
        assert role["tailnet"] is True


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_scripts_are_syntactically_valid(script):
    assert script.exists(), f"{script} missing"
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_hardening_script_covers_every_ticket_requirement():
    text = (ROOT / "harden-armbian.sh").read_text()
    assert "systemctl set-default multi-user.target" in text
    assert "video=HDMI-A-1:d" in text
    assert "power/control" in text and "auto" in text
    assert "rfkill block wifi" in text
    assert "armbian-ramlog" in text
    assert "Storage=volatile" in text
    assert "noatime,commit=600" in text
    assert "RuntimeWatchdogSec=30" in text
    assert "meson_gxbb_wdt" in text


def test_exit_node_script_covers_every_ticket_requirement():
    text = (ROOT / "tf1-a-exit-node.sh").read_text()
    assert "net.ipv4.ip_forward = 1" in text
    assert "rx-udp-gro-forwarding on" in text
    assert "/etc/networkd-dispatcher/routable.d/50-tailscale-gro" in text
    assert "scaling_governor" in text and "performance" in text
    assert "--advertise-exit-node" in text
    assert "--advertise-routes=192.168.8.0/24" in text


def test_adb_host_script_covers_every_ticket_requirement():
    text = (ROOT / "tf1-c-adb-host.sh").read_text()
    assert "adb -a -P 5037 nodaemon server" in text
    assert "tailscale0" in text
    for tool in ("rkdeveloptool", "mtkclient", "pyamlboot", "heimdall-flash", "flashrom"):
        assert tool in text


def test_limpet_testbed_script_covers_every_ticket_requirement():
    text = (ROOT / "tf1-d-limpet-testbed.sh").read_text()
    assert "hal_daemon.py run --foreground" in text
    assert "daemon.py --interval 30" in text
    assert "wq#3786" in text  # MCP meta-server security hold documented
    assert not any(
        "ExecStart" in line and "meta_server.py" in line
        for line in text.splitlines()
    )


def test_external_vantage_covers_every_ticket_requirement():
    launch_gatus = (ROOT / "tf1-b-external-vantage" / "launch-gatus.sh").read_text()
    assert "ghcr.io/twin/gatus" in launch_gatus
    assert "arm64" in launch_gatus
    assert "--tmpfs /data" in launch_gatus

    launch_ntfy = (ROOT / "tf1-b-external-vantage" / "launch-ntfy.sh").read_text()
    assert "ntfy:2.28" in launch_ntfy
    assert '"${TS_IP}:80:80"' in launch_ntfy

    ntfy_cfg = (ROOT / "tf1-b-external-vantage" / "ntfy-server.yml").read_text()
    assert "auth-default-access: \"deny-all\"" in ntfy_cfg


def test_render_includes_hostname_and_services():
    role = DATA["roles"]["tf1-c-adb-host"]
    out = render_role("tf1-c-adb-host", role)
    assert "tf1-c" in out
    assert "adb-server.service" in out


@pytest.mark.parametrize(
    "bad_role",
    [
        {
            "kind": "exit_node",
            "name": "x",
            "hostname": "h",
            "tailnet": True,
            "services": [],
            "advertise_routes": "10.0.0.0/8",
            "cpu_governor": "powersave",
            "sysctl_dropin": "/etc/sysctl.d/x.conf",
        },
        {
            "kind": "external_vantage",
            "name": "x",
            "hostname": "h",
            "tailnet": True,
            "services": [],
            "gatus_image": "ghcr.io/twin/gatus:v5-arm64",
            "ntfy_bind": "0.0.0.0",
            "ntfy_acl": "allow-all",
        },
        {
            "kind": "adb_host",
            "name": "x",
            "hostname": "h",
            "tailnet": False,
            "services": [],
            "adb_bind": "-a -P 5037",
            "adb_mode": "nodaemon",
            "firewall": "tailscale0-only",
            "re_tools": ["rkdeveloptool", "mtkclient", "pyamlboot", "heimdall", "flashrom"],
        },
        {
            "kind": "adb_host",
            "name": "x",
            "hostname": "h",
            "tailnet": True,
            "services": [],
            "adb_bind": "-a -P 5037",
            "adb_mode": "nodaemon",
            "firewall": "tailscale0-only",
            "re_tools": ["rkdeveloptool"],  # missing mtkclient/pyamlboot/heimdall/flashrom
        },
        {
            "kind": "limpet_testbed",
            "name": "x",
            "hostname": "h",
            "tailnet": True,
            "services": [],
            "hal_daemon_service": "limpet-hal-daemon.service",
            "registry_daemon_service": "limpet-registryd.service",
            "meta_server_enabled": True,  # wq#3786 hold forbids this
        },
    ],
)
def test_invalid_roles_are_rejected(bad_role):
    with pytest.raises(RoleValidationError):
        validate_role("bad-role", bad_role)
