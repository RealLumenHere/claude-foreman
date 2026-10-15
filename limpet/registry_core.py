"""LIMPET registry core: pure dependency resolution over a set of installed
plugin manifests. Given the manifests, which names the operator has enabled,
and the latest health results, compute each plugin's *effective* state and a
safe start order. No I/O -- registry.py owns the filesystem/subprocess side.

Effective-state ladder (worst wins), with the reason recorded:
  disabled   -- operator toggled it off (enable/disable, lifecycle step 4)
  blocked    -- a declared dependency is missing, disabled, version-mismatched,
                or the plugin conflicts with another plugin already active
  cycle      -- this plugin sits on a dependency cycle
  unhealthy  -- enabled and deps ok, but its own health check is failing
  active     -- enabled, every dependency satisfied, healthy, no active conflict

Only `active` and `unhealthy` plugins are started; `active` ones are ordered
so every dependency starts before its dependants (lifecycle step 5).
Ref: design/wq3810_limpet_plugin_system.md, manifest.py.
"""
from __future__ import annotations

import dataclasses

from manifest import PluginManifest


@dataclasses.dataclass(frozen=True)
class ResolvedPlugin:
    name: str
    version: str
    kind: str
    category: str       # "" if not categorized (wq#3932 rec. 4)
    state: str          # disabled | blocked | cycle | unhealthy | active
    reason: str         # human-readable justification for non-active states
    dependencies: dict  # name -> constraint, carried through for the unified view
    conflicts: list      # names this plugin cannot be active alongside


@dataclasses.dataclass(frozen=True)
class Resolution:
    plugins: dict          # name -> ResolvedPlugin
    start_order: list      # names of active plugins, dependencies first

    def active(self) -> list:
        return [n for n in self.start_order]

    def by_state(self, state: str) -> list:
        return sorted(n for n, p in self.plugins.items() if p.state == state)


def _find_cycles(manifests: dict) -> set:
    """Return the set of plugin names that lie on at least one dependency
    cycle (only edges to installed plugins count -- a dep on something not
    installed is a `blocked`, not a cycle)."""
    on_cycle = set()
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in manifests}

    def visit(node, stack):
        color[node] = GRAY
        stack.append(node)
        for dep in manifests[node].dependencies:
            if dep not in manifests:
                continue
            if color[dep] == GRAY:
                # dep is an ancestor on the current stack -> cycle back to it
                idx = stack.index(dep)
                on_cycle.update(stack[idx:])
            elif color[dep] == WHITE:
                visit(dep, stack)
        stack.pop()
        color[node] = BLACK

    for name in manifests:
        if color[name] == WHITE:
            visit(name, [])
    return on_cycle


def _symmetric_conflicts(manifests: dict) -> dict:
    """Mirror each declared `conflicts` entry onto its target, so a conflict
    declared on only one side (A declares B) still mutually excludes both --
    an author shouldn't have to coordinate editing both plugins' manifests to
    get the exclusion right (wq#3932 recommendation 5)."""
    out = {name: set(m.conflicts) for name, m in manifests.items()}
    for name, targets in list(out.items()):
        for target in targets:
            if target in out:
                out[target].add(name)
    return out


def resolve(manifests, enabled, health_ok=None) -> Resolution:
    """Resolve effective states.

    manifests: name -> PluginManifest (the installed, valid plugins)
    enabled:   container of names the operator has enabled
    health_ok: name -> bool latest health result; a name absent here (or a
               plugin with no health check) is treated as healthy.
    """
    manifests = dict(manifests)
    enabled = set(enabled)
    health_ok = dict(health_ok or {})
    on_cycle = _find_cycles(manifests)
    conflicts_of = _symmetric_conflicts(manifests)

    resolved = {}

    def dep_problem(m: PluginManifest):
        """First unsatisfied dependency of m, as a reason string, or None.
        A dep is satisfied only if the target is installed, version-matching,
        AND itself resolved `active` -- a dependant cannot run on a dependency
        that is disabled, blocked, on a cycle, or unhealthy. Because we resolve
        in dependency order, every dep's final state is known here, so a block
        propagates transitively up the chain."""
        for dep_name, constraint in m.dependencies.items():
            target = manifests.get(dep_name)
            if target is None:
                return f"dependency {dep_name!r} is not installed"
            if not target.satisfies(constraint):
                return (f"dependency {dep_name!r} {constraint} not satisfied "
                        f"by installed {target.version}")
            dep_state = resolved[dep_name].state
            if dep_state != "active":
                return f"dependency {dep_name!r} is {dep_state}"
        return None

    # Resolve in dependency-first order so dep_problem sees finalized dep states.
    # Cycle members are resolved first (they never appear as a satisfied dep).
    for name in _resolution_order(manifests, on_cycle):
        m = manifests[name]
        if name not in enabled:
            state, reason = "disabled", "operator-disabled"
        elif name in on_cycle:
            state, reason = "cycle", "on a dependency cycle"
        else:
            problem = dep_problem(m)
            active_conflict = next(
                (c for c in sorted(conflicts_of.get(name, ()))
                 if resolved.get(c) is not None and resolved[c].state == "active"),
                None)
            if problem is not None:
                state, reason = "blocked", problem
            elif active_conflict is not None:
                state, reason = "blocked", f"conflicts with {active_conflict!r} (already active)"
            elif health_ok.get(name, True) is False:
                state, reason = "unhealthy", "health check failing"
            else:
                state, reason = "active", ""
        resolved[name] = ResolvedPlugin(
            name=name, version=m.version, kind=m.kind, category=m.category,
            state=state, reason=reason, dependencies=dict(m.dependencies),
            conflicts=list(m.conflicts))

    start_order = _topo_order(
        manifests, [n for n, p in resolved.items() if p.state == "active"])
    return Resolution(plugins=resolved, start_order=start_order)


def _resolution_order(manifests: dict, on_cycle: set) -> list:
    """Order ALL installed plugins for resolution: cycle members first (so a
    dependant reading a cycle member's state finds it already resolved), then
    the remaining plugins deps-first. The non-cycle part is a DAG, so a simple
    post-order DFS suffices; alphabetical tie-break keeps it deterministic."""
    order = list(sorted(on_cycle))
    visited = set(on_cycle)

    def visit(node):
        if node in visited:
            return
        visited.add(node)
        for dep in sorted(manifests[node].dependencies):
            if dep in manifests and dep not in on_cycle:
                visit(dep)
        order.append(node)

    for name in sorted(manifests):
        visit(name)
    return order


def _topo_order(manifests: dict, names) -> list:
    """Order `names` so every dependency precedes its dependants. Inputs are
    known acyclic (callers pass only active plugins, cycles filtered out).
    Ties broken alphabetically for a deterministic, re-runnable order."""
    selected = set(names)
    visited = set()
    order = []

    def visit(node):
        if node in visited:
            return
        visited.add(node)
        for dep in sorted(manifests[node].dependencies):
            if dep in selected:
                visit(dep)
        order.append(node)

    for name in sorted(selected):
        visit(name)
    return order
