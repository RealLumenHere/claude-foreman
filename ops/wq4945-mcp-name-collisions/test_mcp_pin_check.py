"""wq#4945: the cross-server name-collision check must fail on any collision nobody reviewed."""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mcp_pin_check as pc  # noqa: E402

CHECK = os.path.join(HERE, "mcp_pin_check.py")
BASELINE = os.path.join(HERE, "fixtures", "mcp-tool-pins-2026-11.json")  # security-gate 2026-11-12, sha256 b1cad9a5…
GHIDRA = os.path.join(HERE, "..", "wq4946-ghidra-mcp-guard", "ghidra-pins.json")
ACCEPT = os.path.join(HERE, "accepted-collisions.json")
AUDIT_F5 = {"execute_sql", "search_objects", "list_instances", "batch_decompile", "list_bookmarks",
            "list_segments", "list_strings", "search_strings", "set_bookmark", "set_comment"}


def write_pins(tmp_path, name, servers):
    path = tmp_path / name
    path.write_text(json.dumps({"servers": {k: {"tools": {t: "0" * 64 for t in v}} for k, v in servers.items()}}))
    return str(path)


def run(*args):
    return subprocess.run([sys.executable, "-I", CHECK, "collisions", *args], capture_output=True, text=True)


def test_baseline_reproduces_audit_f5():
    report = pc.check([BASELINE])
    assert set(report["unreviewed"]) == AUDIT_F5
    assert report["unreviewed"]["list_instances"] == {"ghidra-lumen": ["list_instances"],
                                                      "stealth-browser": ["list_instances"]}


def test_backend_surface_is_not_agent_visible():
    report = pc.check([BASELINE])
    assert "gitea@guarded (30)" in report["servers"].values()
    assert not any("@backend" in v for v in report["servers"].values())


def test_guard_pins_layer_over_baseline():
    report = pc.check([BASELINE, GHIDRA])
    assert "ghidra-lumen@guarded (202)" in report["servers"].values()
    assert set(report["unreviewed"]) == AUDIT_F5 - {"list_instances"}  # hidden by the wq#4946 allowlist


def test_fleet_today_passes_with_reviewed_set():
    r = run(BASELINE, GHIDRA, "--accept", ACCEPT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 unreviewed, 9 accepted" in r.stdout
    assert "STALE_ACCEPT" not in r.stdout


def test_baseline_alone_fails_on_list_instances():
    r = run(BASELINE, "--accept", ACCEPT)
    assert r.returncode == 1
    assert "UNREVIEWED list_instances: ghidra-lumen: list_instances; stealth-browser: list_instances" in r.stdout


def test_new_server_reusing_a_name_fails(tmp_path):
    newcomer = write_pins(tmp_path, "new.json", {"evil@unguarded": ["git_status", "execute_sql"]})
    r = run(BASELINE, GHIDRA, newcomer, "--accept", ACCEPT)
    assert r.returncode == 1
    assert "UNREVIEWED execute_sql: evil: execute_sql; fleet-memory: execute_sql; fleet-state: execute_sql" in r.stdout


def test_case_and_separator_variants_collide(tmp_path):
    p = write_pins(tmp_path, "p.json", {"a@guarded": ["List-Instances"], "b@unguarded": ["list.instances"],
                                        "c@unguarded": ["list_instance"]})
    report = pc.check([p])
    assert report["unreviewed"] == {"list_instances": {"a": ["List-Instances"], "b": ["list.instances"]}}


def test_same_server_two_surfaces_is_not_a_collision(tmp_path):
    p = write_pins(tmp_path, "p.json", {"a@guarded": ["x"], "a@unguarded": ["x", "y"], "a@backend": ["x", "y"]})
    assert pc.check([p])["unreviewed"] == {}


def test_stale_accept_reported(tmp_path):
    p = write_pins(tmp_path, "p.json", {"a@guarded": ["x"]})
    r = run(p, "--accept", ACCEPT)
    assert r.returncode == 0
    assert "STALE_ACCEPT execute_sql" in r.stdout


@pytest.mark.parametrize("body", ['{"servers": {"a@guarded": {"tools": ["x"]}}}', '{"servers": {"nosurface": {"tools": {}}}}',
                                  '{"nope": 1}', "not json"])
def test_bad_pins_exit_2(tmp_path, body):
    p = tmp_path / "bad.json"
    p.write_text(body)
    r = run(str(p))
    assert r.returncode == 2 and r.stderr.startswith("mcp_pin_check:")


def test_accept_without_reason_exit_2(tmp_path):
    acc = tmp_path / "acc.json"
    acc.write_text(json.dumps({"accepted": [{"name": "x", "servers": ["a", "b"], "reason": " "}]}))
    p = write_pins(tmp_path, "p.json", {"a@guarded": ["x"]})
    assert run(p, "--accept", str(acc)).returncode == 2
