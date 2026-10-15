"""LIMPET plugin manifest: the `limpet-plugin.toml` schema, its parser, a
semantic-version constraint comparator, and a fail-closed validator.

A plugin -- a tool (e.g. Ghidra MCP) or a hardware module (e.g. a HackRF) --
is described by exactly one `limpet-plugin.toml`. Installing a plugin means
dropping that file (or a symlink to it) into the drop-in directory
`/etc/limpet/plugins.d/`; see registry.py for the pickup/register step and
registry_core.py for dependency resolution.

Pure: parse/validate operate on text or dicts only, no filesystem or network,
so the schema can be unit-tested without a plugin tree. Ref: design/wq3810_limpet_plugin_system.md.
"""
from __future__ import annotations

import dataclasses
import re
import tomllib

KINDS = ("tool", "hardware", "data")

# Closed category taxonomy for discovery/browse (wq#3932 recommendation 4):
# every mature plugin ecosystem surveyed there (NSE categories, Binary Ninja /
# ClawHub marketplace categories) selects by category, and a closed enum keeps
# `limpet list --category` and a future index both browsable without authors
# inventing free-text tags. "other" is the deliberate escape hatch for a
# plugin that doesn't fit yet rather than forcing a bad guess.
CATEGORIES = (
    "disassembler", "decompiler", "debugger", "fuzzer", "firmware",
    "sdr", "flash-programmer", "network", "data", "other",
)

# A deliberately small, total version grammar: a constraint is an operator
# (defaulting to ==) followed by a dotted release. "*" / "any" match anything.
# We do NOT pull in a packaging dependency -- stdlib-only is a house rule, and
# plugin authors in the wild (Cargo, apt) get by with exactly these operators.
_CONSTRAINT_RE = re.compile(r"^\s*(>=|<=|==|>|<)?\s*([0-9]+(?:\.[0-9]+)*)\s*$")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclasses.dataclass(frozen=True)
class HealthCheck:
    """How the plugin reports its own liveness. `command` is run by the
    registry on `interval_seconds` cadence; exit 0 means healthy. A plugin
    with no [health] table is treated as always-healthy (nothing to probe)."""
    command: str
    interval_seconds: int = 60
    timeout_seconds: int = 10


@dataclasses.dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str
    kind: str
    summary: str = ""
    maintainer: str = ""
    license: str = ""
    # one of CATEGORIES, or "" for not-yet-categorized (wq#3932 rec. 4)
    category: str = ""
    # name -> version constraint, e.g. {"ghidra": ">=11.0"}
    dependencies: dict = dataclasses.field(default_factory=dict)
    # name -> version constraint for a dep that improves this plugin but is
    # never required to resolve it -- unlike [dependencies], a missing or
    # unsatisfied entry here never blocks (wq#3932 recommendation 5: "soft
    # dependencies"). registry_core.py reports per-entry satisfaction so an
    # operator/agent can see what installing the optional dep would unlock.
    optional_dependencies: dict = dataclasses.field(default_factory=dict)
    # names of other plugins this one cannot be active alongside, e.g. two
    # plugins claiming the same SDR hardware (wq#3932 rec. 5). Declaring the
    # conflict on either side is enough -- resolve() treats it as mutual.
    conflicts: list = dataclasses.field(default_factory=list)
    health: HealthCheck | None = None
    # optional lifecycle hooks the registry invokes on enable/disable
    start: str = ""
    stop: str = ""
    # free-form provenance / hardware hints, preserved but not validated deeply
    source: dict = dataclasses.field(default_factory=dict)
    hardware: dict = dataclasses.field(default_factory=dict)

    def satisfies(self, constraint: str) -> bool:
        """Does this plugin's version satisfy `constraint` (a dependant's ask)?"""
        return version_satisfies(self.version, constraint)


class ManifestError(ValueError):
    """Raised by parse_manifest when a manifest cannot be loaded at all
    (malformed TOML or failing validation). Carries the list of problems."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _parse_release(release: str):
    return tuple(int(p) for p in release.split("."))


def _compare(a: str, b: str) -> int:
    """Compare two dotted releases, zero-padding the shorter. 1.0 == 1.0.0."""
    ta, tb = _parse_release(a), _parse_release(b)
    width = max(len(ta), len(tb))
    ta = ta + (0,) * (width - len(ta))
    tb = tb + (0,) * (width - len(tb))
    return (ta > tb) - (ta < tb)


def version_satisfies(version: str, constraint: str) -> bool:
    """True if `version` meets `constraint`. Unparseable inputs fail closed
    (return False) so a typo in a dep never silently waves the dep through."""
    if constraint is None:
        return True
    c = constraint.strip()
    if c in ("", "*", "any"):
        return True
    m = _CONSTRAINT_RE.match(c)
    if not m or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version.strip()):
        return False
    op, want = m.group(1) or "==", m.group(2)
    cmp = _compare(version.strip(), want)
    return {
        "==": cmp == 0,
        ">": cmp > 0,
        ">=": cmp >= 0,
        "<": cmp < 0,
        "<=": cmp <= 0,
    }[op]


def _validate_deps_table(deps, label: str) -> list:
    """Shared shape check for [dependencies] and [optional_dependencies]:
    both are a table of name -> version constraint, same name/constraint
    grammar. Only how a resolver USES the two differs (registry_core.py)."""
    errors = []
    if not isinstance(deps, dict):
        errors.append(f"{label} must be a table of name = constraint")
        return errors
    for dep_name, constraint in deps.items():
        if not _NAME_RE.match(dep_name):
            errors.append(f"{label} name {dep_name!r} is not a valid plugin name")
        if not isinstance(constraint, str):
            errors.append(f"{label} {dep_name!r} constraint must be a string")
        elif not _CONSTRAINT_RE.match(constraint) and constraint.strip() not in ("*", "any", ""):
            errors.append(f"{label} {dep_name!r} constraint {constraint!r} is not parseable")
    return errors


def validate_manifest(data: dict) -> list:
    """Return a list of human-readable problems with a parsed manifest dict;
    empty list means valid. Used by the registry's register step to reject a
    bad drop-in without crashing the daemon."""
    errors = []
    plugin = data.get("plugin")
    if not isinstance(plugin, dict):
        return ["missing [plugin] table"]

    name = plugin.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name or ""):
        errors.append(
            f"[plugin].name {name!r} must match {_NAME_RE.pattern} "
            "(lowercase, so it is safe as a filename and a dependency key)")

    version = plugin.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version or ""):
        errors.append(f"[plugin].version {version!r} must be a dotted release like 1.2.3")

    kind = plugin.get("kind")
    if kind not in KINDS:
        errors.append(f"[plugin].kind {kind!r} must be one of {KINDS}")

    category = plugin.get("category", "")
    if category not in ("", None) and category not in CATEGORIES:
        errors.append(f"[plugin].category {category!r} must be one of {CATEGORIES} (or omitted)")

    conflicts = plugin.get("conflicts", [])
    if not isinstance(conflicts, list):
        errors.append("[plugin].conflicts must be a list of plugin names")
    else:
        for conflict_name in conflicts:
            if not isinstance(conflict_name, str) or not _NAME_RE.match(conflict_name):
                errors.append(f"conflicts entry {conflict_name!r} is not a valid plugin name")
            elif conflict_name == name:
                errors.append(f"plugin {name!r} cannot declare a conflict with itself")

    errors.extend(_validate_deps_table(data.get("dependencies", {}), "[dependencies]"))
    errors.extend(_validate_deps_table(
        data.get("optional_dependencies", {}), "[optional_dependencies]"))

    health = data.get("health")
    if health is not None:
        if not isinstance(health, dict) or not isinstance(health.get("command"), str) or not health["command"].strip():
            errors.append("[health] must set a non-empty string `command`")
        for field in ("interval_seconds", "timeout_seconds"):
            if field in health and (not isinstance(health[field], int) or health[field] <= 0):
                errors.append(f"[health].{field} must be a positive integer")

    return errors


def manifest_from_dict(data: dict) -> PluginManifest:
    """Build a PluginManifest from an already-validated dict."""
    plugin = data["plugin"]
    health = None
    if isinstance(data.get("health"), dict):
        h = data["health"]
        health = HealthCheck(
            command=h["command"],
            interval_seconds=int(h.get("interval_seconds", 60)),
            timeout_seconds=int(h.get("timeout_seconds", 10)),
        )
    runtime = data.get("runtime", {}) if isinstance(data.get("runtime"), dict) else {}
    return PluginManifest(
        name=plugin["name"],
        version=plugin["version"],
        kind=plugin["kind"],
        summary=plugin.get("summary", ""),
        maintainer=plugin.get("maintainer", ""),
        license=plugin.get("license", ""),
        category=plugin.get("category", "") or "",
        dependencies=dict(data.get("dependencies", {}) or {}),
        optional_dependencies=dict(data.get("optional_dependencies", {}) or {}),
        conflicts=list(plugin.get("conflicts", []) or []),
        health=health,
        start=str(runtime.get("start", "")),
        stop=str(runtime.get("stop", "")),
        source=dict(data.get("source", {}) or {}),
        hardware=dict(data.get("hardware", {}) or {}),
    )


def parse_manifest(toml_text: str) -> PluginManifest:
    """Parse and validate `limpet-plugin.toml` text. Raises ManifestError
    (fail-closed) on malformed TOML or any validation problem."""
    try:
        data = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError([f"invalid TOML: {exc}"]) from exc
    errors = validate_manifest(data)
    if errors:
        raise ManifestError(errors)
    return manifest_from_dict(data)
