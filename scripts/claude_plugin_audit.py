#!/usr/bin/env python3
"""Claude Code plugin registration audit (wq#4883).

Answers "which plugins does Claude Code actually load on this host?" from the
files Claude Code itself reads, instead of from `ls ~/.claude/plugins/`.

A directory under ~/.claude/plugins/ is NOT a loaded plugin. Claude Code loads:
  * builtin plugins (shipped in the binary),
  * account-synced plugins (~/.claude/plugins/synced/<org>_<user>/<id>/),
  * marketplace installs: a record in ~/.claude/plugins/installed_plugins.json
    for a marketplace listed in known_marketplaces.json, enabled by an
    `enabledPlugins["<name>@<marketplace>"]` entry in a settings file,
  * `claude --plugin-dir <dir>` for one session only.
(https://code.claude.com/docs/en/plugins/install, .../plugins/troubleshooting)

Any directory carrying a `.claude-plugin/plugin.json` that is none of the
above is reported as ORPHAN: on disk, never registered, never loaded.

Read-only. Prints JSON; exit 0 when no orphans, 1 when orphans exist.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SKIP_TOP = {"marketplaces", "synced", "cache", "store", ".trash", "repos", "data"}


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _manifest_dirs(root: Path, max_depth: int = 3):
    """Yield dirs under root (excluding Claude-managed subtrees) holding a plugin manifest."""
    if not root.is_dir():
        return
    for top in sorted(root.iterdir()):
        if not top.is_dir() or top.name in SKIP_TOP:
            continue
        stack = [(top, 1)]
        while stack:
            d, depth = stack.pop()
            if (d / ".claude-plugin" / "plugin.json").is_file():
                yield d
                continue
            if depth >= max_depth:
                continue
            try:
                kids = sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
            except OSError:
                continue
            stack.extend((k, depth + 1) for k in reversed(kids))


def _hook_events(plugin_dir: Path):
    data = _load(plugin_dir / "hooks" / "hooks.json")
    if not isinstance(data, dict):
        return {"hooks_json": "missing_or_invalid", "events": [], "problems": []}
    hooks = data.get("hooks") or {}
    problems = []
    for event, groups in hooks.items():
        for g in groups or []:
            m = g.get("matcher") if isinstance(g, dict) else None
            if m is not None and not isinstance(m, str):
                problems.append(f"{event}: matcher is {type(m).__name__}, schema expects a string")
    return {"hooks_json": "ok" if hooks else "empty", "events": sorted(hooks), "problems": problems}


def audit(claude_dir: Path, claude_json: Path) -> dict:
    plugins = claude_dir / "plugins"
    settings = _load(claude_dir / "settings.json") or {}
    enabled = settings.get("enabledPlugins") or {}
    installed_raw = _load(plugins / "installed_plugins.json")
    installed = {}
    if isinstance(installed_raw, dict):
        installed = installed_raw.get("plugins", installed_raw) or {}
    marketplaces = _load(plugins / "known_marketplaces.json") or {}
    usage = (_load(claude_json) or {}).get("pluginUsage") or {}

    installed_paths = set()
    for recs in installed.values():
        for r in recs if isinstance(recs, list) else [recs]:
            if isinstance(r, dict) and r.get("installPath"):
                installed_paths.add(os.path.realpath(r["installPath"]))

    orphans = []
    for d in _manifest_dirs(plugins):
        man = _load(d / ".claude-plugin" / "plugin.json") or {}
        name = man.get("name") or d.name
        registered = os.path.realpath(d) in installed_paths or any(
            k.split("@", 1)[0] == name for k in installed
        )
        if registered:
            continue
        orphans.append({
            "path": str(d),
            "name": name,
            "version": man.get("version"),
            "enabled_key_present": any(k.split("@", 1)[0] == name for k in enabled),
            "ever_loaded": any(k.split("@", 1)[0] == name for k in usage),
            "hooks": _hook_events(d),
        })

    synced = []
    for man_path in sorted((plugins / "synced").glob("*/manifest.json")):
        for p in (_load(man_path) or {}).get("plugins", []):
            synced.append({"name": p.get("name"), "description": (p.get("description") or "")[:60],
                           "installationPreference": p.get("installationPreference")})

    return {
        "plugins_dir": str(plugins),
        "installed_plugins_json": (plugins / "installed_plugins.json").is_file(),
        "installed": sorted(installed),
        "enabledPlugins": enabled,
        "known_marketplaces": sorted(marketplaces),
        "synced": synced,
        "ever_loaded": sorted(usage),
        "orphans": orphans,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    home = Path(os.path.expanduser("~"))
    ap.add_argument("--claude-dir", type=Path, default=home / ".claude")
    ap.add_argument("--claude-json", type=Path, default=home / ".claude.json")
    a = ap.parse_args(argv)
    rep = audit(a.claude_dir, a.claude_json)
    print(json.dumps(rep, indent=2))
    return 1 if rep["orphans"] else 0


if __name__ == "__main__":
    sys.exit(main())
