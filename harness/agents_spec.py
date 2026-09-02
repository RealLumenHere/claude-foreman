"""agents_spec — loader for harness/agents.yaml (wq#1301, foreman pattern D).

Replaces the module-level dicts that used to be hardcoded in fworker.py
(ARCHETYPE_MAX_TURNS, LOCAL_ALLOWED_ARCHETYPES, LOCAL_ARCHETYPE_MAX_TURNS,
WALL_CLOCK_DEFAULT_S/RCA, WORKER_ADD_DIRS) with one declarative, diffable YAML file
per kb/fleet-architecture-ideas.md pattern D. fworker.py loads through this module
instead of hardcoding the values so the archetype config is reviewable in a single
place and lint() can catch drift against research/models.yaml before it ships.

This module is READ-ONLY at import time (fworker.py needs it to behave identically
to a static dict) — see agents_apply.py for the separate lint/apply CLI.
"""
import os
import re

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_YAML = os.environ.get("FOREMAN_AGENTS_YAML", os.path.join(HARNESS_DIR, "agents.yaml"))
MODELS_YAML = os.environ.get(
    "FOREMAN_MODELS_YAML",
    os.path.join(HARNESS_DIR, "..", "research", "models.yaml"))

# mirrors orchestrator/router.py's _RUNG_RE (kept independent on purpose — fworker
# deliberately talks to the orchestrator only via subprocess, never a module import,
# see fworker.py's ROUTER_CLI comment. This is the same regex/format, not the same
# code path).
_RUNG_RE = re.compile(r"^\s*([A-Za-z0-9._-]+)(?:@([a-z]+))?\s*(?:\(([^)]*)\))?\s*$")


class AgentsSpecError(Exception):
    pass


def _load_yaml(path):
    if yaml is None:
        raise AgentsSpecError("PyYAML not installed — cannot load agents.yaml")
    with open(path) as f:
        return yaml.safe_load(f) or {}


class ArchetypeSpec:
    def __init__(self, name, data):
        self.name = name
        self.primary_tuple = data["primary_tuple"]
        self.max_turns = int(data["max_turns"])
        self.wall_clock_timeout_s = int(data["wall_clock_timeout_s"])
        self.prompt_template = data["prompt_template"]
        self.add_dirs = list(data["add_dirs"])
        local = data.get("local")
        self.local_allowed = local is not None
        self.local_max_turns = int(local["max_turns"]) if local else None

    def __repr__(self):
        return f"ArchetypeSpec({self.name!r}, {self.primary_tuple!r})"


class AgentsSpec:
    def __init__(self, raw, path=None):
        self.path = path
        self.raw = raw
        defaults = raw.get("defaults") or {}
        archetypes = raw.get("archetypes") or {}
        if not archetypes:
            raise AgentsSpecError(f"{path}: no archetypes declared")
        self.archetypes = {}
        for name, entry in archetypes.items():
            merged = dict(defaults)
            merged.update(entry or {})
            for required in ("primary_tuple", "max_turns", "wall_clock_timeout_s",
                              "prompt_template", "add_dirs"):
                if required not in merged:
                    raise AgentsSpecError(
                        f"{path}: archetype {name!r} missing {required!r} "
                        f"(not in entry or defaults)")
            self.archetypes[name] = ArchetypeSpec(name, merged)
        self.default_add_dirs = list(defaults.get("add_dirs") or [])

    def get(self, archetype):
        """Raise AgentsSpecError for an unknown archetype — callers that need a
        soft fallback should catch KeyError-shaped lookups via `archetype in spec`
        first (fworker.py's DEFAULT_MAX_TURNS pattern predates this file and stays
        for unlisted/legacy archetype strings)."""
        try:
            return self.archetypes[archetype]
        except KeyError:
            raise AgentsSpecError(f"unknown archetype {archetype!r}") from None

    def __contains__(self, archetype):
        return archetype in self.archetypes

    def names(self):
        return sorted(self.archetypes)


_CACHE = {}


def load(path=None, force=False):
    """Cached by resolved path, mirroring router.load_manifest()'s pattern —
    fworker.py imports this once at module scope, tests pass FOREMAN_AGENTS_YAML to
    point at a fixture without touching the real file."""
    path = path or AGENTS_YAML
    if force or path not in _CACHE:
        _CACHE[path] = AgentsSpec(_load_yaml(path), path=path)
    return _CACHE[path]


def parse_primary_tuple(spec):
    """'sonnet-5@high' -> ('sonnet-5', 'high'); 'qwen36-local@local' -> ('qwen36-local',
    None); 'DECOMPOSE' -> ('DECOMPOSE', None). Same shape router.py's Rung.spec()
    emits, so a models.yaml primary rung string compares equal to this without any
    further normalization."""
    if spec == "DECOMPOSE":
        return "DECOMPOSE", None
    m = _RUNG_RE.match(spec)
    if not m:
        raise AgentsSpecError(f"unparseable primary_tuple {spec!r}")
    model, effort, _cond = m.groups()
    return model, (None if effort == "local" else effort)


def _routing_primary_tuples(models_yaml_path=None):
    """archetype -> primary rung spec string, read directly from research/models.yaml
    `routing:` (first entry of each chain). Independent of router.py by design (see
    module docstring) — this only needs the primary rung, not the full downgrade
    chain / dominated-tuple logic router.py owns."""
    path = models_yaml_path or MODELS_YAML
    data = _load_yaml(path)
    routing = data.get("routing") or {}
    out = {}
    for archetype, chain in routing.items():
        if not chain:
            continue
        first = str(chain[0]).strip()
        # strip a trailing "(condition)" annotation — irrelevant to the primary rung
        first = re.sub(r"\s*\([^)]*\)\s*$", "", first)
        out[archetype] = first
    return out


def lint(spec=None, models_yaml_path=None):
    """-> list[str] problems, empty when clean. Checked by agents_apply.py lint/apply
    and by tests — never called from fworker.py's hot path (import-time load() must
    stay fast and side-effect-free)."""
    spec = spec or load()
    problems = []
    routing = None
    try:
        routing = _routing_primary_tuples(models_yaml_path)
    except (OSError, AgentsSpecError) as e:
        problems.append(f"could not read models.yaml routing table: {e}")

    for name, arch in spec.archetypes.items():
        if arch.max_turns <= 0:
            problems.append(f"{name}: max_turns must be positive, got {arch.max_turns}")
        if arch.wall_clock_timeout_s <= 0:
            problems.append(
                f"{name}: wall_clock_timeout_s must be positive, got {arch.wall_clock_timeout_s}")
        if arch.local_allowed and arch.local_max_turns <= 0:
            problems.append(
                f"{name}: local.max_turns must be positive, got {arch.local_max_turns}")
        prompt_path = os.path.join(HARNESS_DIR, "prompts", arch.prompt_template)
        if not os.path.isfile(prompt_path):
            problems.append(f"{name}: prompt_template {arch.prompt_template!r} not found "
                             f"at {prompt_path}")
        try:
            parse_primary_tuple(arch.primary_tuple)
        except AgentsSpecError as e:
            problems.append(f"{name}: {e}")

        if routing is not None:
            live = routing.get(name)
            if live is None:
                problems.append(
                    f"{name}: declared in agents.yaml but has no routing entry in models.yaml")
            elif live != arch.primary_tuple:
                problems.append(
                    f"{name}: primary_tuple drift — agents.yaml says {arch.primary_tuple!r}, "
                    f"models.yaml routing primary is {live!r}")

    if routing is not None:
        for name in routing:
            if name not in spec.archetypes and name != "orchestrator":
                problems.append(
                    f"{name}: has a models.yaml routing entry but no agents.yaml archetype")

    # wq#1329 (ISSUE-LOG #127): the alias/id resolution bug that ticket fixed only ever
    # showed up in the FULL routing chain (downgrade/escalation rungs beyond the primary
    # tuple the checks above already cover), so it needed its own check. router.py's own
    # Manifest.check() (triggered inside load_manifest()) already walks every rung of
    # every chain and rejects one whose resolved model isn't a real models.yaml id —
    # reuse that instead of re-deriving alias resolution here a second time, which is
    # exactly the two-sources-of-truth pattern that produced #127 in the first place.
    # Local import: lint() is documented as off fworker's hot path, so this doesn't
    # affect the module's import-time cost; the "independent of router.py" promise in
    # this file's docstring is specifically about primary-tuple parsing, not this check.
    try:
        import sys as _sys
        orch_dir = os.path.join(HARNESS_DIR, "..", "orchestrator")
        if orch_dir not in _sys.path:
            _sys.path.insert(0, orch_dir)
        import router as _router
        _router.load_manifest(models_yaml_path or MODELS_YAML, force=True)
    except Exception as e:  # noqa: BLE001 — any manifest-load failure is a lint finding
        problems.append(f"router.py manifest check failed (rung alias/id resolution): {e}")

    return problems
