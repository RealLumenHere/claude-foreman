"""wq#3967 LIMPET OFRAK plugin: unpack / list-resources / patch / repack.

Thin wrapper around the real `ofrak` Python API (OFRAK has no standalone CLI
of its own -- it is a library -- so this module *is* the CLI surface the
cli_wrapper entries invoke). Verified against the upstream examples
(examples/ex1_simple_string_modification.py, examples/ex8_recursive_unpacking.py
in redballoonsecurity/ofrak) rather than guessed.

OFRAK's resource graph is an in-process object model with no documented
cross-run persistence API (checked ofrak_core/src/ofrak/resource.py: no
save/load-session method exists), so a single CLI invocation cannot resume
another invocation's live unpacked tree. The four verbs below are split to
match what IS safely stateless across process boundaries:

  unpack         -- real OFRAK unpack_recursively(); writes a resource-tree
                     manifest (workdir/resources.json) and keeps a pristine
                     copy of the input (workdir/original.bin) for patch to
                     re-derive from. No persistence trick: unpacking is cheap
                     and deterministic, so re-deriving it in `patch` is not a
                     missing feature, it's the only sound option.
  list_resources -- pure read of resources.json. No `ofrak` import needed.
  patch          -- re-opens original.bin, unpack_recursively() again, locates
                     the target resource, applies BinaryPatchModifier,
                     pack_recursively(), flushes to workdir/patched.bin. This
                     is necessarily one OFRAK run because patch and repack
                     share the same live resource tree.
  repack         -- delivers workdir/patched.bin to the caller's requested
                     output path with sha256 provenance. Pure file op.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil


class OfrakOpsError(RuntimeError):
    pass


def _import_ofrak():
    """Import point kept separate so tests can monkeypatch it without the
    real (restrictively-licensed, Python-version-sensitive) package installed."""
    try:
        import ofrak
        import ofrak.core as ofrak_core
    except ImportError as exc:
        raise OfrakOpsError(
            "the 'ofrak' package is not installed (see README.md Licensing)"
        ) from exc
    return ofrak, ofrak_core


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def unpack(input_path: str, workdir: str) -> dict:
    if not os.path.isfile(input_path):
        raise OfrakOpsError(f"input file not found: {input_path}")
    ofrak_pkg, _ = _import_ofrak()

    os.makedirs(workdir, exist_ok=True)
    original_copy = os.path.join(workdir, "original.bin")
    shutil.copyfile(input_path, original_copy)

    entries: list = []

    async def _main(ofrak_context, path):
        root = await ofrak_context.create_root_resource_from_file(path)
        await root.unpack_recursively()
        for descendant in await root.get_descendants():
            data_range = await descendant.get_data_range_within_root()
            entries.append(
                {
                    "id": descendant.get_id().hex(),
                    "caption": descendant.get_caption(),
                    "tags": sorted(tag.__name__ for tag in descendant.get_tags(inherit=False)),
                    "start": data_range.start,
                    "end": data_range.end,
                }
            )

    ofrak_pkg.OFRAK().run(_main, original_copy)

    manifest = {"input_path": input_path, "resource_count": len(entries), "resources": entries}
    manifest_path = os.path.join(workdir, "resources.json")
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)

    return {"workdir": workdir, "resource_count": len(entries), "manifest": manifest_path}


def list_resources(workdir: str, tag: str | None = None) -> dict:
    manifest_path = os.path.join(workdir, "resources.json")
    if not os.path.isfile(manifest_path):
        raise OfrakOpsError(f"no resources.json in {workdir} -- run unpack first")
    with open(manifest_path) as fh:
        manifest = json.load(fh)
    resources = manifest["resources"]
    if tag:
        resources = [r for r in resources if tag in r["tags"]]
    return {"count": len(resources), "resources": resources}


def _patch(workdir: str, *, offset: int | None, data: bytes | None,
           old_string: str | None, new_string: str | None,
           resource_id: str | None) -> dict:
    original_copy = os.path.join(workdir, "original.bin")
    if not os.path.isfile(original_copy):
        raise OfrakOpsError(f"no original.bin in {workdir} -- run unpack first")
    ofrak_pkg, ofrak_core = _import_ofrak()

    result: dict = {}

    async def _main(ofrak_context, path):
        root = await ofrak_context.create_root_resource_from_file(path)
        await root.unpack_recursively()

        target = root
        if resource_id is not None:
            match = None
            for descendant in await root.get_descendants():
                if descendant.get_id().hex() == resource_id:
                    match = descendant
                    break
            if match is None:
                raise OfrakOpsError(f"no resource with id {resource_id!r}")
            target = match

        target_data = await target.get_data()
        if old_string is not None:
            old_bytes = old_string.encode()
            new_bytes = new_string.encode()
            if len(new_bytes) != len(old_bytes):
                raise OfrakOpsError(
                    "patch-strings only supports a same-byte-length replacement "
                    "(in-place patch, no resize)"
                )
            found = target_data.find(old_bytes)
            if found < 0:
                raise OfrakOpsError(f"string {old_string!r} not found in target resource")
            patch_offset, patch_bytes = found, new_bytes
        else:
            patch_offset, patch_bytes = offset, data

        config = ofrak_core.BinaryPatchConfig(patch_offset, patch_bytes)
        await target.run(ofrak_core.BinaryPatchModifier, config)

        await root.pack_recursively()
        patched_path = os.path.join(workdir, "patched.bin")
        await root.flush_data_to_disk(patched_path)

        result.update({"patched_path": patched_path, "offset": patch_offset,
                        "bytes_changed": len(patch_bytes)})

    ofrak_pkg.OFRAK().run(_main, original_copy)
    return result


def patch_bytes(workdir: str, offset: int, hex_bytes: str, resource_id: str | None = None) -> dict:
    return _patch(workdir, offset=offset, data=bytes.fromhex(hex_bytes),
                  old_string=None, new_string=None, resource_id=resource_id)


def patch_strings(workdir: str, old_string: str, new_string: str,
                   resource_id: str | None = None) -> dict:
    return _patch(workdir, offset=None, data=None, old_string=old_string,
                  new_string=new_string, resource_id=resource_id)


def repack(workdir: str, output_path: str) -> dict:
    patched_path = os.path.join(workdir, "patched.bin")
    if not os.path.isfile(patched_path):
        raise OfrakOpsError(f"no patched.bin in {workdir} -- run patch-bytes/patch-strings first")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    shutil.copyfile(patched_path, output_path)

    original_copy = os.path.join(workdir, "original.bin")
    return {
        "output_path": output_path,
        "sha256": _sha256(output_path),
        "original_sha256": _sha256(original_copy) if os.path.isfile(original_copy) else None,
    }
