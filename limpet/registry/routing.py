"""wq#3970 -- target-class -> toolset routing table.

Split from wq#3944/A4, design/wq3929_competitive_analysis.md section 5 A4
(reverse-skill scenario routing): make the target-class -> toolset map a
declared, inspectable table instead of an agent-chosen-each-time decision.
This is the table the meta-server's two-level router expands on demand
(design/wq3806_limpet_architecture.md:152-159 -- "the agent asks for the
toolset relevant to the current target class ... and only those schemas are
mounted") and that the Target Device Wizard identify step
(limpet/target_devices.py, limpet/target-devices.d) can use once it has
classified a target.

The table itself (``routing.json``) is pure data; this module only loads,
validates, and resolves it. The meta-server and the wizard's identify step
are design-only so far (design/wq3806_limpet_architecture.md section 2), so
this stays pure and offline-testable, same posture as model_router.py
(wq#3968, split from the sibling wq#3944/A5): the caller takes the returned
toolset groups and does the actual plugin-schema mounting itself.
"""
from __future__ import annotations

import json
import pathlib

from .import_catalog import load_catalog

DEFAULT_ROUTING_TABLE = str(pathlib.Path(__file__).parent / "routing.json")
DEFAULT_CATALOG = str(pathlib.Path(__file__).parent.parent / "tool-catalog.json")


class RoutingTableError(ValueError):
    """The routing table failed schema validation. Lists every problem found."""


class RoutingError(ValueError):
    """A target class is not in the routing table -- not a guess, a fail-closed."""


def load_routing_table(path: str = DEFAULT_ROUTING_TABLE) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def validate_routing_table(table: dict) -> list[str]:
    """Schema-check a routing table. Returns a list of problem strings (empty if valid)."""
    errors: list[str] = []

    if not isinstance(table, dict):
        return [f"routing table must be a mapping, got {type(table).__name__}"]

    if table.get("schema_version") != 1:
        errors.append(f"schema_version must be 1, got {table.get('schema_version')!r}")

    target_classes = table.get("target_classes")
    if not isinstance(target_classes, dict) or not target_classes:
        errors.append("target_classes must be a non-empty mapping")
        return errors

    for class_id, entry in target_classes.items():
        src = f"target_classes[{class_id!r}]"
        if not isinstance(entry, dict):
            errors.append(f"{src}: must be a mapping")
            continue

        if not isinstance(entry.get("description"), str) or not entry["description"]:
            errors.append(f"{src}: description must be a non-empty string")

        groups = entry.get("toolset_groups")
        if not isinstance(groups, list) or not groups:
            errors.append(f"{src}: toolset_groups must be a non-empty list")
            continue

        seen_stages: set[str] = set()
        for i, group in enumerate(groups):
            gsrc = f"{src}.toolset_groups[{i}]"
            if not isinstance(group, dict):
                errors.append(f"{gsrc}: must be a mapping")
                continue

            stage = group.get("stage")
            if not isinstance(stage, str) or not stage:
                errors.append(f"{gsrc}: stage must be a non-empty string")
            elif stage in seen_stages:
                errors.append(f"{src}: duplicate stage {stage!r}")
            else:
                seen_stages.add(stage)

            tools = group.get("tools")
            if not isinstance(tools, list) or not tools:
                errors.append(f"{gsrc}: tools must be a non-empty list")
            elif not all(isinstance(t, str) and t for t in tools):
                errors.append(f"{gsrc}: tools must be a list of non-empty strings")

    return errors


def load_catalog_tool_names(catalog_path: str = DEFAULT_CATALOG) -> set[str]:
    return {entry["name"] for entry in load_catalog(catalog_path) if entry.get("name")}


def validate_tools_resolve(table: dict, catalog_tool_names: set[str]) -> list[str]:
    """Every tool id referenced anywhere in the table must be a known catalog tool name."""
    errors: list[str] = []
    for class_id, entry in table.get("target_classes", {}).items():
        for group in entry.get("toolset_groups", []):
            for tool in group.get("tools", []):
                if tool not in catalog_tool_names:
                    errors.append(
                        f"target_classes[{class_id!r}] stage {group.get('stage')!r}: "
                        f"tool {tool!r} is not in the tool catalog")
    return errors


def resolve_target_class(table: dict, target_class: str) -> list[dict]:
    """Return the ordered toolset groups for `target_class`, or raise RoutingError."""
    target_classes = table.get("target_classes", {})
    if target_class not in target_classes:
        raise RoutingError(
            f"no routing entry for target class {target_class!r} -- known classes: "
            f"{sorted(target_classes)}")
    return target_classes[target_class]["toolset_groups"]
