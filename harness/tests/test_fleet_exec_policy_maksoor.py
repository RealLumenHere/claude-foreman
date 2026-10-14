"""wq#3857: fleet-health filed "[disk] maksoor root filesystem at 91%" as a read-only
du investigation, but maksoor was absent from allow.ssh and `du` from
run_readonly_cmds, so no grant could carry the probe. Both are added; maksoor stays
read-only (no restart, no copy target) and du gets no special-casing past the
generic argv shape / -c checks.
"""
import os
import sys

sys.path.insert(0, "/home/lumen/bin")
import fleet_exec_policy  # noqa: E402

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_PATH = os.path.join(HARNESS_DIR, "fleet-exec-policy.yaml")


def _load_policy():
    return fleet_exec_policy.load_policy(POLICY_PATH)


def test_maksoor_is_ssh_readonly_only():
    policy = _load_policy()
    assert "maksoor" in policy["allow"]["ssh"]
    assert "maksoor" not in policy["allow"].get("restart", [])
    assert "maksoor" not in policy.get("copy_dirs", {})


def test_du_disk_triage_argv_allowed():
    ok, rule, _ = fleet_exec_policy.check_run(
        "maksoor", ["du", "-xh", "--max-depth=2", "/"], _load_policy())
    assert ok, rule
    assert rule == "policy:run_readonly_cmds"


def test_du_does_not_open_neighbours():
    policy = _load_policy()
    assert not fleet_exec_policy.check_run("maksoor", ["find", "/", "-size", "+1G"], policy)[0]
    assert not fleet_exec_policy.check_run("maksoor", ["du", "-sh", "/;", "rm"], policy)[0]
    assert not fleet_exec_policy.check_run("maksoor", ["du", "-c", "/"], policy)[0]
