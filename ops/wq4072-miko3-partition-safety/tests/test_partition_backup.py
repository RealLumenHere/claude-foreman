"""Tests for ops/wq4072-miko3-partition-safety/partition_backup.py (wq#4072).

No hardware required: the write-guard and manifest logic are pure functions over the
local filesystem. The guard tests are the load-bearing ones given the ticket's
zero-tolerance-for-blind-writes constraint (three units already bricked) — they prove
the one thing this toolkit must never do (write to the device) is actually blocked,
not just documented.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import partition_backup as pb  # noqa: E402


def test_guard_allows_read_direction_dd():
    pb.guard_command(["dd", "if=/dev/block/by-name/boot", "of=/data/local/tmp/boot.img", "bs=4M"])


def test_guard_blocks_write_to_device_node():
    with pytest.raises(pb.GuardError):
        pb.guard_command(["dd", "if=/data/local/tmp/boot.img", "of=/dev/block/by-name/boot", "bs=4M"])


def test_guard_blocks_write_to_mmcblk_device():
    with pytest.raises(pb.GuardError):
        pb.guard_command(["dd", "if=/tmp/image.img", "of=/dev/mmcblk0", "bs=4M"])


@pytest.mark.parametrize("verb_cmd", [
    ["fastboot", "flash", "boot", "boot.img"],
    ["fastboot", "erase", "userdata"],
    ["adb", "shell", "format"],
    ["mtk", "wipe", "cache"],
    ["adb", "push", "patched.img", "/data/local/tmp/x"],
])
def test_guard_blocks_write_verbs(verb_cmd):
    with pytest.raises(pb.GuardError):
        pb.guard_command(verb_cmd)


def test_guard_allows_adb_pull_and_getvar():
    pb.guard_command(["adb", "pull", "/data/local/tmp/boot.img", "./boot.img"])
    pb.guard_command(["fastboot", "getvar", "all"])


def test_manifest_round_trip(tmp_path):
    (tmp_path / "boot.img").write_bytes(b"boot-partition-bytes")
    (tmp_path / "recovery.img").write_bytes(b"recovery-partition-bytes-longer")

    manifest = pb.build_manifest(str(tmp_path))
    assert set(manifest.entries) == {"boot.img", "recovery.img"}
    assert manifest.entries["boot.img"].size_bytes == len(b"boot-partition-bytes")

    out = tmp_path / "manifest.json"
    pb.write_manifest(manifest, str(out))
    on_disk = json.loads(out.read_text())
    assert on_disk["entries"]["boot.img"]["sha256"] == manifest.entries["boot.img"].sha256


def test_verify_clean(tmp_path):
    (tmp_path / "boot.img").write_bytes(b"boot-partition-bytes")
    manifest = pb.build_manifest(str(tmp_path))
    pb.write_manifest(manifest, str(tmp_path / "manifest.json"))

    assert pb.verify_manifest(str(tmp_path / "manifest.json"), str(tmp_path)) == []


def test_verify_detects_corruption(tmp_path):
    (tmp_path / "boot.img").write_bytes(b"boot-partition-bytes")
    manifest = pb.build_manifest(str(tmp_path))
    pb.write_manifest(manifest, str(tmp_path / "manifest.json"))

    (tmp_path / "boot.img").write_bytes(b"CORRUPTED-DIFFERENT-BYTES")
    problems = pb.verify_manifest(str(tmp_path / "manifest.json"), str(tmp_path))
    assert any("SHA256 MISMATCH" in p for p in problems)


def test_verify_detects_missing_file(tmp_path):
    (tmp_path / "boot.img").write_bytes(b"boot-partition-bytes")
    (tmp_path / "recovery.img").write_bytes(b"recovery-partition-bytes")
    manifest = pb.build_manifest(str(tmp_path))
    pb.write_manifest(manifest, str(tmp_path / "manifest.json"))

    os.remove(tmp_path / "recovery.img")
    problems = pb.verify_manifest(str(tmp_path / "manifest.json"), str(tmp_path))
    assert any("MISSING: recovery.img" in p for p in problems)


def test_verify_detects_untracked_file(tmp_path):
    (tmp_path / "boot.img").write_bytes(b"boot-partition-bytes")
    manifest = pb.build_manifest(str(tmp_path))
    pb.write_manifest(manifest, str(tmp_path / "manifest.json"))

    (tmp_path / "surprise.img").write_bytes(b"not in the manifest")
    problems = pb.verify_manifest(str(tmp_path / "manifest.json"), str(tmp_path))
    assert any("UNTRACKED: surprise.img" in p for p in problems)
