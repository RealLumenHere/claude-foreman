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

# Closed taxonomy for a plugin's mutation boundary (wq#3998, adopted from REA
# morluto/rea's documented "analysis runs locally ... never escalates" boundary:
# REA states it operates only on processes it owns and installs an engine only
# "with approval"). A plugin DECLARES what it may touch so the trust gate can see
# a plugin's blast radius before enabling it, and an agent can report it to the
# operator -- the boundary is a contract, not a runtime observation. Scopes widen
# left-to-right: "none" (read-only analysis, REA's own default) < "workspace"
# (writes only into the investigation workspace) < "target" (modifies the
# artifact under analysis, e.g. OFRAK repack) < "device" (writes to attached
# hardware, e.g. a flash programmer) < "host" (mutates the Limpet host itself).
WRITE_SCOPES = ("none", "workspace", "target", "device", "host")

# Closed capability taxonomy for per-step model routing (wq#3968, split from
# wq#3944/A5, design/wq3929_competitive_analysis.md §5 A5). "general" is the
# resident chat/reasoning model and never needs a route -- it IS the fallback
# (design/wq3806_limpet_architecture.md:260-266). The other three name the
# specialized steps the competitive analysis found have purpose-built, open-
# weight alternatives (fleet-search 2026-10-15, limpet/examples/llm4decompile.toml:
# MIT+DeepSeek-licensed, GGUF quants from 1.3B-33B comfortably under the
# 32GB/iGPU ceiling): "re-naming" (function/symbol name recovery), "re-types"
# (variable/struct type inference), "vision" (HAL frame/screen analysis, §4).
# ReCopilot itself (arXiv 2505.16366) is NOT adopted as a route target --
# wq#3995 found its only served weights are a China-hosted API
# (tqgpt.qianxin.com), an unacceptable egress for decompiled target code -- so
# this taxonomy is the mechanism, deliberately provider-agnostic.
MODEL_CAPABILITIES = ("general", "re-naming", "re-types", "vision")

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
class MutationBoundary:
    """A plugin's declared no-sudo mutation boundary (wq#3998, from REA
    morluto/rea). REA's boundary -- "analysis runs locally", operates only on
    owned processes, never escalates privilege -- maps here to the all-default
    instance: escalates=False, network=False, writes=("none",). A plugin that
    needs more declares it, so the trust gate never has to INFER blast radius
    from a tool's name: a flash programmer declares writes=("device",), an
    OFRAK-style repack plugin writes=("target",), and escalates=True is the
    explicit, auditable opt-out of the no-sudo default rather than a silent
    `sudo` buried in a start hook."""
    escalates: bool = False            # needs root / privilege escalation
    network: bool = False              # contacts the network (REA: local-only)
    writes: tuple = ("none",)          # subset of WRITE_SCOPES this plugin mutates


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
    # declared no-sudo mutation boundary (wq#3998). None = not declared; the
    # registry/trust gate treats an undeclared boundary as unknown rather than
    # as safe, so "no [mutation] table" is not silently equivalent to read-only.
    mutation: MutationBoundary | None = None
    # the authoritative upstream for this plugin (wq#3998 anti-impersonation,
    # from REA morluto/rea: "A similar repository name or copied README does not
    # establish that a download is official"). A plugin index/registry compares a
    # drop-in's declared source against the canonical URL registered for the
    # name, so a name-squatting copy with an identical README is detectable. ""
    # = not declared. Read from [source].canonical.
    canonical_source: str = ""
    # free-form provenance / hardware hints, preserved but not validated deeply
    source: dict = dataclasses.field(default_factory=dict)
    hardware: dict = dataclasses.field(default_factory=dict)
    # step name -> one of MODEL_CAPABILITIES (wq#3968). A step with no entry
    # here is "general" by default -- model_router.py resolves the actual
    # model, this table only declares what a step NEEDS.
    model_routes: dict = dataclasses.field(default_factory=dict)

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


def _validate_mutation(mutation) -> list:
    """Shape check for the optional [mutation] table (wq#3998). Fail-closed: an
    unknown write scope or a non-bool flag is rejected rather than coerced, so a
    typo like writes=["devices"] can never widen a plugin's declared boundary."""
    errors = []
    if not isinstance(mutation, dict):
        errors.append("[mutation] must be a table")
        return errors
    for flag in ("escalates", "network"):
        if flag in mutation and not isinstance(mutation[flag], bool):
            errors.append(f"[mutation].{flag} must be a boolean")
    if "writes" in mutation:
        writes = mutation["writes"]
        if not isinstance(writes, list) or not writes:
            errors.append("[mutation].writes must be a non-empty list of write scopes")
        else:
            for scope in writes:
                if scope not in WRITE_SCOPES:
                    errors.append(
                        f"[mutation].writes entry {scope!r} must be one of {WRITE_SCOPES}")
            if "none" in writes and len(writes) > 1:
                errors.append('[mutation].writes "none" cannot be combined with other scopes')
    return errors


def _validate_model_routes(routes) -> list:
    """Shape check for the optional [model_routes] table (wq#3968): step name
    -> capability, where capability must be a declared MODEL_CAPABILITIES
    value. Fail-closed like _validate_mutation -- a typo'd capability (e.g.
    "re-names") is rejected rather than silently treated as "general"."""
    errors = []
    if not isinstance(routes, dict):
        errors.append("[model_routes] must be a table of step = capability")
        return errors
    for step_name, capability in routes.items():
        if not isinstance(step_name, str) or not step_name.strip():
            errors.append(f"[model_routes] step name {step_name!r} must be a non-empty string")
        if capability not in MODEL_CAPABILITIES:
            errors.append(
                f"[model_routes].{step_name} capability {capability!r} "
                f"must be one of {MODEL_CAPABILITIES}")
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

    if data.get("mutation") is not None:
        errors.extend(_validate_mutation(data["mutation"]))

    if data.get("model_routes") is not None:
        errors.extend(_validate_model_routes(data["model_routes"]))

    source = data.get("source", {})
    if isinstance(source, dict) and "canonical" in source:
        if not isinstance(source["canonical"], str) or not source["canonical"].strip():
            errors.append("[source].canonical must be a non-empty string (the authoritative upstream)")

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
    mutation = None
    if isinstance(data.get("mutation"), dict):
        mut = data["mutation"]
        mutation = MutationBoundary(
            escalates=bool(mut.get("escalates", False)),
            network=bool(mut.get("network", False)),
            writes=tuple(mut.get("writes", ("none",))),
        )
    src = data.get("source", {}) if isinstance(data.get("source"), dict) else {}
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
        mutation=mutation,
        canonical_source=str(src.get("canonical", "")),
        source=dict(data.get("source", {}) or {}),
        hardware=dict(data.get("hardware", {}) or {}),
        model_routes=dict(data.get("model_routes", {}) or {}),
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
