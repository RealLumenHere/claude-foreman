"""wq#3807 — registry watcher: rebuild the unified manifest when tools.d changes.

In production the systemd ``limpet-registry.path`` unit is the event-driven
trigger (it runs ``limpet-registry build`` on any change under tools.d). This
module is the standalone, dependency-free equivalent for hosts without that
unit: it polls the drop-in directory's signature and rebuilds on change.
"""

from __future__ import annotations

import os
import time

from .registry import DEFAULT_MANIFEST, DEFAULT_TOOLS_DIR, rebuild


def _signature(tools_dir: str) -> tuple:
    """A cheap fingerprint of the drop-in directory: the (name, mtime_ns, size)
    of every manifest file. Changes whenever a tool is added, removed, or edited."""
    sig = []
    try:
        entries = sorted(os.listdir(tools_dir))
    except OSError:
        return ()
    for entry in entries:
        if not entry.endswith((".yaml", ".yml")):
            continue
        try:
            st = os.stat(os.path.join(tools_dir, entry))
        except OSError:
            continue
        sig.append((entry, st.st_mtime_ns, st.st_size))
    return tuple(sig)


def watch(tools_dir: str = DEFAULT_TOOLS_DIR,
          manifest_path: str = DEFAULT_MANIFEST, *,
          interval: float = 2.0, max_iterations: int | None = None) -> int:
    """Rebuild once, then poll ``tools_dir`` and rebuild whenever it changes.

    Returns the number of rebuilds performed. ``max_iterations`` bounds the poll
    loop (tests pass a small value); ``None`` runs until interrupted.
    """
    last_sig = _signature(tools_dir)
    _, errors = rebuild(tools_dir, manifest_path, force=True)
    for err in errors:
        print(f"limpet-registry: {err}", flush=True)
    rebuilds = 1

    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        time.sleep(interval)
        sig = _signature(tools_dir)
        if sig == last_sig:
            continue
        last_sig = sig
        _, errors = rebuild(tools_dir, manifest_path)
        rebuilds += 1
        for err in errors:
            print(f"limpet-registry: {err}", flush=True)
    return rebuilds
