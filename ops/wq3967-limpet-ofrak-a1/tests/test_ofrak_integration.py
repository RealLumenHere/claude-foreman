"""wq#3967 -- integration test against the real `ofrak` package.

Unlike test_ofrak_ops.py (OFRAK monkeypatched), this drives the actual OFRAK
unpack_recursively/pack_recursively engine against the committed fixture
tests/fixtures/sample.tar (one file, greeting.txt, inside a plain USTAR tar).
A mock can't catch a real OFRAK API drift (wrong method name, changed
BinaryPatchConfig signature) the way flashrom's test_flashrom_integration.py
argues for its own real-binary tests.

Skipped when `ofrak` isn't importable -- which is the case on this worker's
sandbox Python (3.14): ofrak pins an aiohttp version whose Cython extension
doesn't build against 3.14's C API (ISSUE-LOG.md). A compatible interpreter
(3.12, available at /home/lumen/.local/bin/python3.12 on this host) is what
install.sh's venv step targets for real deployment.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

try:
    import ofrak  # noqa: F401
    _OFRAK_AVAILABLE = True
except ImportError:
    _OFRAK_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _OFRAK_AVAILABLE, reason="ofrak package not installed")

_OPS_PATH = Path(__file__).resolve().parent.parent / "ofrak_ops.py"
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample.tar"


def _load_ops():
    spec = importlib.util.spec_from_file_location("ofrak_ops_integration", _OPS_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ofrak_ops_integration"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_unpack_lists_the_tar_member_real_ofrak(tmp_path):
    ops = _load_ops()
    workdir = tmp_path / "work"

    result = ops.unpack(str(_FIXTURE), str(workdir))

    assert result["resource_count"] >= 1
    listing = ops.list_resources(str(workdir))
    captions = [r["caption"] for r in listing["resources"]]
    assert any("greeting.txt" in c for c in captions)


def test_unpack_patch_repack_round_trip_changes_only_patched_bytes_real_ofrak(tmp_path):
    ops = _load_ops()
    workdir = tmp_path / "work"
    output = tmp_path / "repacked.tar"

    ops.unpack(str(_FIXTURE), str(workdir))
    ops.patch_strings(str(workdir), "OFRAK fixture", "OFRAK patched")
    result = ops.repack(str(workdir), str(output))

    original = _FIXTURE.read_bytes()
    repacked = output.read_bytes()

    assert len(original) == len(repacked)
    assert result["sha256"] != result["original_sha256"]

    diff_offsets = [i for i in range(len(original)) if original[i] != repacked[i]]
    assert diff_offsets, "repack produced a byte-identical file -- patch did not apply"

    patched_region = original[diff_offsets[0]: diff_offsets[-1] + 1]
    assert b"fixture" in patched_region or b"patched" in patched_region
    # every differing byte sits inside one contiguous run, i.e. the patch
    # region -- nothing outside it moved.
    assert diff_offsets == list(range(diff_offsets[0], diff_offsets[-1] + 1))
    assert repacked[: diff_offsets[0]] == original[: diff_offsets[0]]
    assert repacked[diff_offsets[-1] + 1:] == original[diff_offsets[-1] + 1:]
