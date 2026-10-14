"""wq#3807 — self-registration: a tool drops its own manifest into tools.d.

A tool's install script calls this (``limpet-registry register --from x.yaml``)
to publish its capabilities. We validate first, then write atomically into the
drop-in directory as ``<name>.yaml``. The registry watcher notices the new file
and rebuilds the unified manifest; the installer does not touch tools.json.
"""

from __future__ import annotations

import os
import tempfile

import yaml

from . import schema
from .registry import DEFAULT_TOOLS_DIR


def register_tool(manifest: dict, tools_dir: str = DEFAULT_TOOLS_DIR,
                  *, overwrite: bool = True) -> str:
    """Validate ``manifest`` and write it as ``<name>.yaml`` in ``tools_dir``.

    Returns the path written. Raises :class:`schema.ManifestError` on an invalid
    manifest and ``FileExistsError`` if the tool is already registered and
    ``overwrite`` is False.
    """
    tool = schema.normalize_tool(manifest, source="<register>")
    os.makedirs(tools_dir, exist_ok=True)
    dest = os.path.join(tools_dir, f"{tool['name']}.yaml")
    if os.path.exists(dest) and not overwrite:
        raise FileExistsError(f"{dest} already exists (pass overwrite to replace)")

    fd, tmp = tempfile.mkstemp(dir=tools_dir, prefix=f".{tool['name']}.", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"# LIMPET tool manifest for {tool['name']} "
                     "(schema v%d). Registered by the tool itself.\n" % schema.SCHEMA_VERSION)
            yaml.safe_dump(tool, fh, sort_keys=True, default_flow_style=False)
        os.replace(tmp, dest)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return dest


def register_from_file(path: str, tools_dir: str = DEFAULT_TOOLS_DIR,
                       *, overwrite: bool = True) -> str:
    """Load a manifest from a YAML file and register it."""
    with open(path, encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh)
    return register_tool(manifest, tools_dir, overwrite=overwrite)


def unregister_tool(name: str, tools_dir: str = DEFAULT_TOOLS_DIR) -> bool:
    """Remove a tool's drop-in file (called on uninstall). Returns True if a
    file was removed."""
    dest = os.path.join(tools_dir, f"{name}.yaml")
    if os.path.exists(dest):
        os.unlink(dest)
        return True
    return False
