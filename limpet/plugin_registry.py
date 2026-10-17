"""LIMPET registry daemon (lifecycle steps 1-5, I/O side).

  1. Install   -- an author drops `<name>.toml` (or a symlink) into the
                  drop-in dir, default /etc/limpet/plugins.d/.
  2. Register  -- scan() loads every drop-in, validates it (manifest.py), and
                  rejects the bad ones without taking the daemon down.
  3. Health    -- refresh_health() runs each enabled plugin's own check.
  4. Enable/   -- enable()/disable() flip a name in a persisted state file, so a
     disable      plugin can be toggled without deleting its drop-in.
  5. Unified   -- reconcile() resolves dependencies (registry_core.py) and writes
     manifest     the unified manifest to state_dir/unified.json atomically.

State lives under state_dir (default /var/lib/limpet): enabled.json (operator
toggles), health.json (last results), unified.json (the resolved view other
tools read). Both dirs are overridable via constructor args or the env vars
LIMPET_PLUGINS_DIR / LIMPET_STATE_DIR so the whole thing is testable in a
tmp tree. Ref: design/wq3810_limpet_plugin_system.md.
"""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import time

from health import run_health_check
from manifest import ManifestError, parse_manifest
import registry_core
import registry_trust

DEFAULT_PLUGINS_DIR = "/etc/limpet/plugins.d"
DEFAULT_STATE_DIR = "/var/lib/limpet"


@dataclasses.dataclass
class RegisterReport:
    loaded: dict          # name -> PluginManifest
    rejected: dict        # drop-in path -> list[error]


def _atomic_write_json(path: str, obj) -> None:
    """Write JSON to `path` via a temp file + rename, so a reader never sees a
    half-written unified manifest (the daemon rewrites it on every reconcile)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(obj, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Registry:
    def __init__(self, plugins_dir=None, state_dir=None, health_runner=None, clock=None,
                 trust_gate=None):
        self.plugins_dir = (
            plugins_dir or os.environ.get("LIMPET_PLUGINS_DIR") or DEFAULT_PLUGINS_DIR)
        self.state_dir = (
            state_dir or os.environ.get("LIMPET_STATE_DIR") or DEFAULT_STATE_DIR)
        self._health_runner = health_runner
        self._clock = clock or time.time
        # Re-verify-on-load (wq#4094, plugin-registry-spec §5.2.4): every scan
        # re-checks each drop-in against its install receipt -- content hash +
        # signature + tier/privilege policy -- so a tampered-on-disk plugin
        # fails to LOAD, not just to install. The default gate is benign for
        # hand-dropped manifests with no receipt (pre-registry workflow);
        # pass trust_gate=False to disable, or a callable to substitute.
        if trust_gate is None:
            trust_gate = registry_trust.trust_gate(self.state_dir)
        self._trust_gate = trust_gate or None

    # ---- paths -----------------------------------------------------------
    @property
    def enabled_path(self):
        return os.path.join(self.state_dir, "enabled.json")

    @property
    def health_path(self):
        return os.path.join(self.state_dir, "health.json")

    @property
    def unified_path(self):
        return os.path.join(self.state_dir, "unified.json")

    # ---- step 1+2: install / register -----------------------------------
    def scan(self) -> RegisterReport:
        """Load and validate every `*.toml` drop-in. A manifest whose declared
        [plugin].name disagrees with its filename, or that fails validation, is
        rejected (recorded, skipped) rather than crashing the scan."""
        loaded, rejected = {}, {}
        if not os.path.isdir(self.plugins_dir):
            return RegisterReport(loaded, rejected)
        for entry in sorted(os.listdir(self.plugins_dir)):
            if not entry.endswith(".toml"):
                continue
            path = os.path.join(self.plugins_dir, entry)
            try:
                with open(path, encoding="utf-8") as fh:
                    manifest = parse_manifest(fh.read())
            except ManifestError as exc:
                rejected[path] = exc.errors
                continue
            except OSError as exc:
                rejected[path] = [f"could not read drop-in: {exc}"]
                continue
            stem = entry[: -len(".toml")]
            if manifest.name != stem:
                rejected[path] = [
                    f"[plugin].name {manifest.name!r} must match the drop-in "
                    f"filename stem {stem!r}"]
                continue
            if manifest.name in loaded:
                rejected[path] = [f"duplicate plugin name {manifest.name!r}"]
                continue
            if self._trust_gate is not None:
                trust_errors = self._trust_gate(manifest.name, manifest)
                if trust_errors:
                    rejected[path] = trust_errors
                    continue
            loaded[manifest.name] = manifest
        return RegisterReport(loaded, rejected)

    # ---- step 4: enable / disable ---------------------------------------
    def _load_enabled_map(self) -> dict:
        try:
            with open(self.enabled_path, encoding="utf-8") as fh:
                data = json.load(fh)
            return {k: bool(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def enabled_names(self, loaded) -> set:
        """A plugin is enabled unless the state file says otherwise -- install
        implies enable (lifecycle step 1->4), and only an explicit disable
        turns it off. Stale entries for uninstalled plugins are ignored."""
        toggles = self._load_enabled_map()
        return {name for name in loaded if toggles.get(name, True)}

    def set_enabled(self, name: str, value: bool) -> None:
        toggles = self._load_enabled_map()
        toggles[name] = bool(value)
        _atomic_write_json(self.enabled_path, toggles)

    def enable(self, name: str) -> None:
        self.set_enabled(name, True)

    def disable(self, name: str) -> None:
        self.set_enabled(name, False)

    # ---- step 3: health --------------------------------------------------
    def refresh_health(self, loaded, only=None) -> dict:
        """Run health checks for the given plugins (default: all loaded),
        persist and return name -> bool. `only` restricts to a subset."""
        results = {}
        for name, manifest in loaded.items():
            if only is not None and name not in only:
                continue
            res = run_health_check(manifest, now=self._clock(), runner=self._health_runner)
            results[name] = res.ok
        merged = self._load_health_map()
        merged.update(results)
        _atomic_write_json(self.health_path, merged)
        return merged

    def _load_health_map(self) -> dict:
        try:
            with open(self.health_path, encoding="utf-8") as fh:
                data = json.load(fh)
            return {k: bool(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    # ---- step 5: reconcile into the unified manifest --------------------
    def reconcile(self, run_health=True) -> dict:
        """The daemon's main loop body: scan drop-ins, (optionally) refresh
        health, resolve dependencies, and write unified.json atomically.
        Returns the unified manifest dict."""
        report = self.scan()
        enabled = self.enabled_names(report.loaded)
        if run_health:
            health = self.refresh_health(report.loaded, only=enabled)
        else:
            health = self._load_health_map()
        resolution = registry_core.resolve(report.loaded, enabled, health_ok=health)
        unified = {
            "generated_at": self._clock(),
            "plugins_dir": self.plugins_dir,
            "start_order": resolution.start_order,
            "plugins": {
                name: {
                    "version": rp.version,
                    "kind": rp.kind,
                    "category": rp.category,
                    "state": rp.state,
                    "reason": rp.reason,
                    "dependencies": rp.dependencies,
                    "conflicts": rp.conflicts,
                    "optional_dependencies": rp.optional_dependencies,
                    "optional_dependencies_satisfied": rp.optional_dependencies_satisfied,
                }
                for name, rp in sorted(resolution.plugins.items())
            },
            "rejected": report.rejected,
        }
        _atomic_write_json(self.unified_path, unified)
        return unified
