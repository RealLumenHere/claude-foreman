"""wq#3807 — LIMPET tool-registry manifest schema.

A tool manifest describes one installed RE tool well enough that an agent can
decide, without trying, whether the tool can handle a given job: what it does
(capabilities), what it eats and emits (input/output formats), where it runs
(architectures, hardware), how to reach it (path, mcp_endpoint), and how to
prove it still works (health_check).

Per-tool manifests live one-file-per-tool under a drop-in directory
(default ``/etc/limpet/tools.d``), the same lineage as ``*.d`` config dirs
(``sysctl.d``, ``udev/rules.d``). The registry reads them all and rebuilds a
single machine-readable ``tools.json`` the agent loads.

The schema is intentionally small and flat so a tool's install script can emit
its own manifest by hand (see ``register.py``).
"""

from __future__ import annotations

SCHEMA_VERSION = 1

# A field is required if an agent cannot safely use the tool without it:
# the name to refer to it, the path to invoke it, what it can do, and a
# command that proves it is actually installed and runnable.
REQUIRED_FIELDS = ("name", "path", "capabilities", "health_check")

# Optional fields with their default when a manifest omits them. Lists default
# empty (unknown == "makes no claim"), scalars to None/"".
OPTIONAL_DEFAULTS = {
    "version": None,
    "description": "",
    "category": "",
    "input_formats": (),
    "output_formats": (),
    "architectures": (),
    "hardware_requirements": {},
    "mcp_endpoint": None,
}

LIST_FIELDS = ("capabilities", "input_formats", "output_formats", "architectures")

# A tool name is used as a filename stem and a JSON key, so keep it to a
# conservative, path-safe, lowercase slug.
_NAME_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789-_+.")


class ManifestError(ValueError):
    """A manifest failed validation. The message lists every problem found."""


def _is_str_list(value) -> bool:
    return isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value)


def validate_tool(manifest, *, source: str = "<manifest>") -> list[str]:
    """Return a list of human-readable problems with ``manifest`` (empty == valid).

    Does not raise; callers decide whether to skip the tool or fail the build.
    """
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return [f"{source}: manifest must be a mapping, got {type(manifest).__name__}"]

    for field in REQUIRED_FIELDS:
        if field not in manifest or manifest[field] in (None, "", [], ()):
            errors.append(f"{source}: missing required field {field!r}")

    name = manifest.get("name")
    if isinstance(name, str):
        if not name:
            errors.append(f"{source}: name is empty")
        elif not set(name) <= _NAME_ALLOWED:
            bad = "".join(sorted(set(name) - _NAME_ALLOWED))
            errors.append(f"{source}: name {name!r} has illegal chars {bad!r} "
                          "(allowed: lowercase, digits, - _ + .)")
    elif name is not None:
        errors.append(f"{source}: name must be a string")

    for field in ("path", "health_check"):
        value = manifest.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"{source}: {field} must be a string")

    for field in LIST_FIELDS:
        if field in manifest and manifest[field] not in (None, ()) and not _is_str_list(manifest[field]):
            errors.append(f"{source}: {field} must be a list of strings")

    hw = manifest.get("hardware_requirements")
    if hw is not None and not isinstance(hw, dict):
        errors.append(f"{source}: hardware_requirements must be a mapping")

    mcp = manifest.get("mcp_endpoint")
    if mcp is not None and not isinstance(mcp, str):
        errors.append(f"{source}: mcp_endpoint must be a string")

    unknown = set(manifest) - set(REQUIRED_FIELDS) - set(OPTIONAL_DEFAULTS)
    if unknown:
        errors.append(f"{source}: unknown field(s) {sorted(unknown)} "
                      "(extend schema.OPTIONAL_DEFAULTS to add one)")
    return errors


def normalize_tool(manifest, *, source: str = "<manifest>") -> dict:
    """Validate and return a canonical dict with every field present and typed.

    Lists become sorted-order-preserving ``list``s, missing optionals get their
    default. Raises :class:`ManifestError` if validation fails.
    """
    errors = validate_tool(manifest, source=source)
    if errors:
        raise ManifestError("; ".join(errors))

    out: dict = {"name": manifest["name"], "path": manifest["path"]}
    for field in LIST_FIELDS:
        value = manifest.get(field) or ()
        out[field] = list(value)
    out["health_check"] = manifest["health_check"]
    for field, default in OPTIONAL_DEFAULTS.items():
        if field in LIST_FIELDS:
            continue
        value = manifest.get(field, default)
        out[field] = dict(value) if isinstance(default, dict) and value else value
    return out
