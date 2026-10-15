"""Unit tests for ofrak_ops.py with `ofrak` monkeypatched -- these run with no
real `ofrak` package installed (it isn't, here: Python 3.14 breaks its pinned
aiohttp build, see ISSUE-LOG.md). They cover the argument validation and the
list/repack file logic that needs no OFRAK call at all, plus the unpack/patch
call shape against a fake OFRAK stand-in. test_ofrak_integration.py is the
real-package counterpart.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_OPS_PATH = Path(__file__).resolve().parent.parent / "ofrak_ops.py"
_spec = importlib.util.spec_from_file_location("ofrak_ops_unit", _OPS_PATH)
ops = importlib.util.module_from_spec(_spec)
sys.modules["ofrak_ops_unit"] = ops
_spec.loader.exec_module(ops)


class FakeRange:
    def __init__(self, start, end):
        self.start = start
        self.end = end


class FakeResource:
    """Enough of ofrak.Resource to drive unpack()/_patch()'s call shape."""

    def __init__(self, data: bytes, caption: str, tags: list, start: int, end: int,
                 res_id: bytes = b"\x00" * 4):
        self._data = data
        self._caption = caption
        self._tags = tags
        self._range = FakeRange(start, end)
        self._id = res_id
        self.patches: list = []

    def get_id(self) -> bytes:
        return self._id

    def get_caption(self) -> str:
        return self._caption

    def get_tags(self, inherit=True):
        return self._tags

    async def get_data_range_within_root(self):
        return self._range

    async def get_data(self):
        return self._data

    async def unpack_recursively(self):
        pass

    async def pack_recursively(self):
        pass

    async def get_descendants(self):
        return [self]

    async def run(self, modifier_cls, config):
        offset, patch_bytes = config
        self._data = self._data[:offset] + patch_bytes + self._data[offset + len(patch_bytes):]
        self.patches.append((offset, patch_bytes))

    async def flush_data_to_disk(self, path):
        Path(path).write_bytes(self._data)


class FakeContext:
    def __init__(self, root: FakeResource):
        self._root = root

    async def create_root_resource_from_file(self, path):
        return self._root


class FakeOFRAK:
    def __init__(self, root: FakeResource):
        self._context = FakeContext(root)

    def run(self, main_coro_func, *args):
        import asyncio
        return asyncio.run(main_coro_func(self._context, *args))


class FakeTag:
    __name__ = "File"


def _patch_ofrak(monkeypatch, root: FakeResource):
    class fake_ofrak_module:
        OFRAK = staticmethod(lambda: FakeOFRAK(root))

    class fake_ofrak_core_module:
        class BinaryPatchConfig:
            def __new__(cls, offset, data):
                return (offset, data)

        BinaryPatchModifier = object()

    monkeypatch.setattr(ops, "_import_ofrak", lambda: (fake_ofrak_module, fake_ofrak_core_module))


def test_unpack_writes_manifest(tmp_path, monkeypatch):
    root = FakeResource(b"Hello, World!\n", "root", [FakeTag()], 0, 14)
    _patch_ofrak(monkeypatch, root)
    input_file = tmp_path / "in.bin"
    input_file.write_bytes(b"Hello, World!\n")
    workdir = tmp_path / "work"

    result = ops.unpack(str(input_file), str(workdir))

    assert result["resource_count"] == 1
    manifest = json.loads((workdir / "resources.json").read_text())
    assert manifest["resources"][0]["caption"] == "root"
    assert manifest["resources"][0]["tags"] == ["File"]
    assert (workdir / "original.bin").read_bytes() == b"Hello, World!\n"


def test_unpack_missing_input_fails_loudly(tmp_path, monkeypatch):
    with pytest.raises(ops.OfrakOpsError, match="not found"):
        ops.unpack(str(tmp_path / "nope.bin"), str(tmp_path / "work"))


def test_list_resources_without_unpack_fails_loudly(tmp_path):
    with pytest.raises(ops.OfrakOpsError, match="run unpack first"):
        ops.list_resources(str(tmp_path))


def test_list_resources_filters_by_tag(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    manifest = {
        "resources": [
            {"id": "aa", "caption": "a", "tags": ["File"], "start": 0, "end": 1},
            {"id": "bb", "caption": "b", "tags": ["TarArchive"], "start": 0, "end": 1},
        ]
    }
    (workdir / "resources.json").write_text(json.dumps(manifest))

    result = ops.list_resources(str(workdir), tag="TarArchive")

    assert result["count"] == 1
    assert result["resources"][0]["caption"] == "b"


def test_patch_bytes_without_unpack_fails_loudly(tmp_path):
    with pytest.raises(ops.OfrakOpsError, match="run unpack first"):
        ops.patch_bytes(str(tmp_path), 0, "ff")


def test_patch_bytes_writes_patched_file(tmp_path, monkeypatch):
    root = FakeResource(b"Hello, World!\n", "root", [FakeTag()], 0, 14)
    _patch_ofrak(monkeypatch, root)
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "original.bin").write_bytes(b"Hello, World!\n")

    result = ops.patch_bytes(str(workdir), 7, "4f4652414b21")  # "OFRAK!"

    assert result["bytes_changed"] == 6
    assert (workdir / "patched.bin").read_bytes() == b"Hello, OFRAK!\n"


def test_patch_strings_requires_equal_length_replacement(tmp_path, monkeypatch):
    root = FakeResource(b"Hello, World!\n", "root", [FakeTag()], 0, 14)
    _patch_ofrak(monkeypatch, root)
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "original.bin").write_bytes(b"Hello, World!\n")

    with pytest.raises(ops.OfrakOpsError, match="same-byte-length"):
        ops.patch_strings(str(workdir), "World", "Universe")


def test_patch_strings_replaces_in_place(tmp_path, monkeypatch):
    root = FakeResource(b"Hello, World!\n", "root", [FakeTag()], 0, 14)
    _patch_ofrak(monkeypatch, root)
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "original.bin").write_bytes(b"Hello, World!\n")

    result = ops.patch_strings(str(workdir), "World", "OFRAK")

    assert result["offset"] == 7
    assert (workdir / "patched.bin").read_bytes() == b"Hello, OFRAK!\n"


def test_repack_without_patch_fails_loudly(tmp_path):
    with pytest.raises(ops.OfrakOpsError, match="run patch-bytes"):
        ops.repack(str(tmp_path), str(tmp_path / "out.bin"))


def test_repack_copies_patched_file_with_provenance(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "original.bin").write_bytes(b"Hello, World!\n")
    (workdir / "patched.bin").write_bytes(b"Hello, OFRAK!\n")
    output = tmp_path / "out" / "final.bin"

    result = ops.repack(str(workdir), str(output))

    assert output.read_bytes() == b"Hello, OFRAK!\n"
    assert result["sha256"] != result["original_sha256"]
