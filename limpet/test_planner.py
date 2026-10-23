"""wq#4509 -- tests for the dynamic system-prompt builder.

Each section of Planner.build_system_prompt is probed at both its populated and its
absent extreme (HAL daemon up/down, workspace open/not-a-workspace, calls made/none
yet) so a regression that only works for the happy path -- e.g. one that assumes the
HAL socket always exists -- fails here instead of on the live box.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import planner as pl  # noqa: E402
from workspace_console import log_path  # noqa: E402


@pytest.fixture
def workspace(tmp_path):
    return str(tmp_path / "ws")


def _write_manifest(workspace, **overrides):
    manifest = {
        "target_id": "acme-router-v2",
        "class": "firmware",
        "identity": {"vendor": "Acme", "model": "Router", "version": "2.1"},
        "hardware_bound": [{"capability": "debug_probe", "adapter": "ST-Link/V2"}],
    }
    manifest.update(overrides)
    os.makedirs(workspace, exist_ok=True)
    with open(os.path.join(workspace, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)


def _append_block(workspace, seq, tool, ok=True, coverage=None):
    block = {
        "tool": tool,
        "started_at": f"2026-01-01T00:0{seq}:00.000000Z",
        "finished_at": f"2026-01-01T00:0{seq}:01.000000Z",
        "ok": ok,
        "coverage": coverage,
        "evidence_id": f"ev_{seq:064d}",
        "untrusted_content": False,
    }
    path = log_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(block) + "\n")


def _point_hal_at(monkeypatch, tmp_path, *, socket_exists, devices=None):
    sock = tmp_path / "hal.sock"
    if socket_exists:
        sock.touch()
    monkeypatch.setattr(pl, "HAL_SOCKET", str(sock))
    inventory = tmp_path / "hardware.json"
    if devices is not None:
        inventory.write_text(json.dumps(devices))
    monkeypatch.setattr(pl, "HAL_INVENTORY_PATH", str(inventory))


def test_no_workspace_only_base_and_hardware_sections(monkeypatch, tmp_path):
    _point_hal_at(monkeypatch, tmp_path, socket_exists=False)

    prompt = pl.Planner().build_system_prompt()

    assert prompt.startswith(pl.BASE_SYSTEM_PROMPT)
    assert "## Attached hardware" in prompt
    assert "None detected" in prompt
    assert "## Active workspace" not in prompt
    assert "## Recent tool results" not in prompt


def test_hal_daemon_down_reports_none_detected(monkeypatch, tmp_path):
    _point_hal_at(monkeypatch, tmp_path, socket_exists=False, devices=[{"name": "ST-Link"}])

    prompt = pl.Planner().build_system_prompt()

    assert "None detected (HAL daemon not running" in prompt


def test_hal_inventory_lists_devices(monkeypatch, tmp_path):
    devices = [{
        "hw_type": "debug_probe", "name": "ST-Link/V2", "capabilities": ["jtag", "swd"],
        "device_node": "/dev/ttyACM0", "status": "connected", "usb_id": "0483:3748",
        "descriptor": "STMicroelectronics ST-Link",
    }]
    _point_hal_at(monkeypatch, tmp_path, socket_exists=True, devices=devices)

    prompt = pl.Planner().build_system_prompt()

    assert "ST-Link/V2 (debug_probe) at /dev/ttyACM0 [0483:3748]" in prompt
    assert "capabilities=[jtag, swd]" in prompt
    assert "descriptor=STMicroelectronics ST-Link" in prompt


def test_hal_inventory_masks_credential_shaped_descriptor(monkeypatch, tmp_path):
    secret = "gh" "p_" + "A" * 36
    devices = [{"name": "rogue-dev", "hw_type": "serial", "capabilities": [],
                "device_node": "/dev/ttyUSB0", "status": "connected", "usb_id": "0000:0000",
                "descriptor": f"vendor string {secret}"}]
    _point_hal_at(monkeypatch, tmp_path, socket_exists=True, devices=devices)

    prompt = pl.Planner().build_system_prompt()

    assert secret not in prompt


def test_missing_manifest_reports_not_a_workspace(monkeypatch, tmp_path, workspace):
    _point_hal_at(monkeypatch, tmp_path, socket_exists=False)

    prompt = pl.Planner(workspace=workspace).build_system_prompt()

    assert "## Active workspace\nNo manifest.json" in prompt


def test_manifest_section_reports_target_identity(monkeypatch, tmp_path, workspace):
    _point_hal_at(monkeypatch, tmp_path, socket_exists=False)
    _write_manifest(workspace)

    prompt = pl.Planner(workspace=workspace).build_system_prompt()

    assert "target_id=acme-router-v2 class=firmware" in prompt
    assert "vendor=Acme" in prompt
    assert "hardware_bound=(ST-Link/V2)" in prompt


def test_warm_band_empty_when_no_calls_yet(monkeypatch, tmp_path, workspace):
    _point_hal_at(monkeypatch, tmp_path, socket_exists=False)
    _write_manifest(workspace)

    prompt = pl.Planner(workspace=workspace).build_system_prompt()

    assert "## Recent tool results (warm band)\nNone yet." in prompt


def test_warm_band_shows_only_the_most_recent_window(monkeypatch, tmp_path, workspace):
    _point_hal_at(monkeypatch, tmp_path, socket_exists=False)
    _write_manifest(workspace)
    for seq, tool in enumerate(["a", "b", "c", "d", "e", "f", "g"], start=1):
        _append_block(workspace, seq, tool)

    prompt = pl.Planner(workspace=workspace).build_system_prompt()

    section = prompt.split("## Recent tool results (warm band)\n", 1)[1]
    tools_shown = [line.split("\t")[1] for line in section.strip().splitlines()]
    assert tools_shown == ["c", "d", "e", "f", "g"]


def test_build_system_prompt_rebuilds_fresh_each_call(monkeypatch, tmp_path, workspace):
    _point_hal_at(monkeypatch, tmp_path, socket_exists=False)
    _write_manifest(workspace)
    planner = pl.Planner(workspace=workspace)

    first = planner.build_system_prompt()
    _append_block(workspace, 1, "extract_rootfs")
    second = planner.build_system_prompt()

    assert "## Recent tool results (warm band)\nNone yet." in first
    assert "extract_rootfs" in second
    assert first != second
