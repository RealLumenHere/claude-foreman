"""wq#3807 — LIMPET tool-registry manifest subsystem.

A machine-readable catalog of the RE tools installed on the Limpet node. Tools
self-register one YAML manifest each into a drop-in directory
(``/etc/limpet/tools.d``); the registry validates them against a small schema
and rebuilds a single ``tools.json`` an agent loads to know what it can do.
"""

from . import schema
from .registry import build_manifest, load_tool_dir, rebuild
from .register import register_tool, unregister_tool

__all__ = [
    "schema",
    "build_manifest",
    "load_tool_dir",
    "rebuild",
    "register_tool",
    "unregister_tool",
]
