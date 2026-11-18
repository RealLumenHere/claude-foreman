#!/usr/bin/env python3
"""router — the foreman's orchestrator tier: archetype -> (model, effort) routing.

BUILD_PLAN.md §T3 (wq#617). Everything here is a pure function of research/models.yaml
(the capability manifest + routing table, FOREMAN_RESEARCH §10/§15) plus whatever the
caller says about the world (governor pressure, prior fails, subsystem spread). No quota
is spent: the router decides, fworker (T2a) spawns.

Doctrine, in the order it is applied:
  1. archetype -> primary rung from models.yaml `routing:`; the chain after it is the
     downgrade / escalation path (FOREMAN_RESEARCH §15 per-archetype lines).
  2. dominated_never_select is a hard guard: a dominated (model, effort) is NEVER emitted,
     not as primary, not as a downgrade, not as an escalation. If a manifest edit ever puts
     one in a chain, load_manifest() refuses to load rather than route around it.
  3. Soft budget pressure ('tight') does NOT change the tuple. A hard governor deny for a
     model skips straight to the next model in the chain — a lower effort cannot un-deny a
     pool — but 'tight' is handled one tier up, by the dispatcher's burn-rate concurrency
     ceiling (dispatcher/burn_rate.py), which paces the fleet without touching any ticket's
     (model, effort). wq#1723 measured the effort-first downgrade this replaced
     (reports/wq1723_effort_first_downgrade.md): a downgraded run is 9.4% cheaper but needs
     9.0% more attempts, so spend per ticket is flat ($2.75 vs $2.79) while success drops
     52.3% -> 48.0% and the reopen rate goes 4.3% -> 10.5%. See §10 CONFIRMED in
     FOREMAN_RESEARCH for the original claim this retires.
  4. Escalation rungs are annotated in the manifest ("(escalate-on-fail)",
     "(2-fails-or-cross-subsystem)", "(on-contradiction)") and only become eligible when
     the caller's facts satisfy the annotation.
  5. Refusal awareness (CLAUDE_CLI_INTERNALS §C): the CLI itself re-routes a refused turn
     (bio -> opus-5, cyber -> opus-4-8, opus-5-origin bio = hard refuse). The router never
     SELECTS opus-4-8 (dominated by opus-5) but reports it as keep_entitled and can tell a
     supervisor where a refusal will land (`refusal_landing`).
  6. Security exclusion (wq#5083, Frank 2026-11-18): a ticket that touches RE / exploit /
     firmware / security work (models.yaml `security_exclusion.tags`, is_security_ticket)
     NEVER runs on Fable. Its archetype chain is swapped for `security_exclusion.chain`
     (opus-4-6, then opus-5-5), any never-prefixed rung is refused in select(), and a stale
     Fable stamp on such a ticket is re-routed by dispatch_cmd() and fworker (security_guard).

CLI (all read-only except `assign`, which writes the tuple into wq via `wq edit`):
    router.py classify  <ticket-id> | --title T [--detail D] [--lane L]
    router.py route     <archetype> [--attempt N] [--fails N] [--cross-subsystem]
                        [--pressure MODEL=ok|tight|deny ...] [--live] [--security]
    router.py assign    <ticket-id> [--archetype A] [--dry-run] [--live]
    router.py dispatch-cmd <ticket-id> [--repo PATH]    # the fworker command line;
                        --repo derives from repo_for_ticket() when omitted — the
                        ticket's lane (LANE_REPO), then a repo-sweep ticket's own
                        repo hint (wq#1094/wq#1123)
    router.py roster    # entitled/routed/dominated table
    router.py check     # manifest self-check (what the probe runs)
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "harness"))
import concurrency  # noqa: E402  # wq#2081: is_local_model for the governor-exempt local rung
import repo_sweep  # noqa: E402  # wq#1123: shared repo_for_ticket() resolver
import needs as needs_mod  # noqa: E402  # wq#1331: per-ticket needs:repo/needs:dir tags
import capability_lint  # noqa: E402  # wq#4686: VERIFY_HOME_ROOT_READ_ALLOW / homeroot_refusal
sys.path.insert(0, "/home/lumen/bin")
import fleet_exec_policy  # noqa: E402  # wq#4686: THOR_HARDLOCK / check_ssh_node for verify needs
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dispatcher"))
import local_capacity  # noqa: E402  # wq#2133: the backend_cooldown.json reader the rung gate shares
import cli_canary  # noqa: E402  # wq#2527: holds a rung the installed claude CLI can't drive

FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
MANIFEST_PATH = os.environ.get(
    "FOREMAN_MODELS_YAML",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "research", "models.yaml"))
# wq#713: lane -> canonical repo, so dispatch-cmd never has to guess (and a caller never
# has to remember) which working tree a ticket's fworker should be pointed at. /home/lumen
# itself is NOT a git repo (no .git) — anything landing there crashes fworker's own
# git-worktree-add step, which is exactly the failure this table exists to prevent.
# Sources: LANE_BOUNDARIES.md §1 (wq#512, pokemon lane owns /home/lumen/pokemon); the
# 'build' lane's own tickets are foreman work and dispatch off FOREMAN_ROOT (this file's
# lane, as this very ticket's worktree shows).
# Lanes not listed here have no single canonical repo (or none confirmed) — dispatch-cmd
# refuses to guess and requires an explicit --repo instead.
# 'auto' (wq's CLI default lane, `wq add` with no --lane) spans arbitrary repos — wq#688
# targeted questfree, wq#306 vault, wq#545 wq-tooling — so wq#830 (d14677e) dropped it from
# this table after a FOREMAN_ROOT default made `dispatch-cmd <auto-ticket>` emit a silently
# wrong --repo. 1eb5c48 (wq#2103/2056, 2026-09-25) put it back so auto-lane infra tickets
# stop dying as lane_unmapped (12 were stuck that way), and that default immediately
# swallowed the per-ticket sweep repo (ISSUE-LOG #841, wq#2753). The contract since
# wq#2753 (3d33058), which repo_for_ticket implements and test_router.py pins:
#   1. per-ticket data outranks this table for EVERY lane — a `needs: repo` tag, a
#      deliverable path, a "lands via lumen-pr against <repo>" line, a verify-repo line or
#      a repo-sweep target in the ticket's own text (each validated against
#      repo_sweep.managed_repos() before use, so still never a guess);
#   2. only then the lane default below — for 'auto' that is FOREMAN_ROOT, on the
#      wq#2103 reading that an auto ticket naming no repo at all is fleet/infra work like
#      the fleet/ops lanes.
# So an auto ticket whose deliverable lives in another repo must SAY so (any of the
# hints in 1); one that stays silent is routed to foreman, not refused. wq#2773: this
# paragraph used to still carry the wq#830 "do not re-add" warning against the live
# entry below; the entry has not had the per-entry Frank confirmation the others carry.
LANE_REPO = {
    "build": FOREMAN_ROOT,
    "foreman-tok": FOREMAN_ROOT,   # Frank confirmed repo path 2026-08-21 (DESIGN §12.1)
    "foreman-auto": FOREMAN_ROOT,  # Frank confirmed repo path 2026-08-21 (DESIGN §12.1)
    "pokemon": "/home/lumen/pokemon",
    "lpp": "/home/lumen/pokemon",        # legacy alias kept so a hand-typed lane still resolves;
                                         # wq#912/wq#929: every LPP ticket ever filed uses lane
                                         # 'pokemon' (0 tickets have ever used 'lpp'), so the dead
                                         # 'lpp' dispatcher-config entry was dropped — this map
                                         # entry is not a config lane and is never scanned
    "questfree": "/home/lumen/questfree",
    "nest": "/home/lumen/projects/nest/NoLongerEvil-Thermostat",  # wq#1155/ISSUE-LOG #56 class:
                                           # [nest]-titled tickets were dispatching lane=build ->
                                           # FOREMAN_ROOT with zero access to the actual nest repo.
                                           # wq#1325/ISSUE-LOG #125: the bare /home/lumen/projects/nest
                                           # dir above is itself just an archive repo of RE notes;
                                           # the real git-tracked firmware repo (its own history/remote,
                                           # deliberately .gitignore'd out of the archive per bd5c4f1)
                                           # lives one level deeper, hence the nested path here.
    "pstack": "/home/lumen/pstack",
    "mission-control": "/home/lumen/mission-control",
    "fleet-spend": FOREMAN_ROOT,
    "fleet": FOREMAN_ROOT,   # wq#1414: fleet-infra work (bin/ scripts, cron guards, monitors)
                             # lands in foreman like ops/fleet-fires/fleet-spend below — the 4
                             # open fleet tickets at ticket time (wq#1296/1297/1133/1373) are all
                             # foreman-repo deliverables. Was previously unmapped, so every fleet
                             # ticket was skipped as lane_unmapped on every dispatch pass
                             # (research/NIGHTLY_DISPATCH_RESILIENCE.md §1.6, dispatcher.log).
    "ops": FOREMAN_ROOT,
    "auto": FOREMAN_ROOT,          # wq#2103/2056: auto-lane infra (gatus/langfuse) lands in foreman, like fleet/ops
    "quest-repair-research": FOREMAN_ROOT,
    "fleet-fires": FOREMAN_ROOT,          # fires are fleet/infra work, run from foreman
    "research": FOREMAN_ROOT,             # cwd is FOREMAN_ROOT itself, so research docs land in
                                           # research/NAME.md (NOT foreman/research/NAME.md, which
                                           # double-nests to FOREMAN_ROOT/foreman/research/) (wq#1334;
                                           # exec-capability doc PR#237; wq#1976 fixed this misleading
                                           # comment after it caused 5 docs to double-nest)
    "pilot": FOREMAN_ROOT,                # wq#1559: pilot cards/reports land in foreman/pilots/<proposal-id>/.
                                          # The EXPERIMENT runs on a fleet node (hathor first), but the
                                          # artifacts of record — CARD.md, REPORT.md — are foreman files,
                                          # so the worktree a pilot worker gets is the foreman repo.
    "mailbot": "/home/lumen/mailbot",     # lumen@ email agent (wq#1163 v2, wq#1164-1174); Frank 2026-09-01 "go ahead with the build"
}
WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
GOVERNOR_CLI = os.environ.get("FOREMAN_GOVERNOR_CLI",
                               os.path.join(FOREMAN_ROOT, "governor", "foreman_usaged.py"))
STATE_JSON = os.environ.get("FOREMAN_STATE_JSON", os.path.join(FOREMAN_ROOT, "state.json"))
FWORKER = os.path.join(FOREMAN_ROOT, "harness", "fworker.py")

EFFORT_LADDER = ["low", "medium", "high", "xhigh", "max"]

# wq#2133: the LiteLLM group over the Macs (dispatcher/local_capacity.py resolves it to a
# member at admit time) and the config.json block that names its members.
LOCAL_WORKER = "local-worker"
DISPATCHER_CONFIG = os.environ.get(
    "FOREMAN_DISPATCHER_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dispatcher", "config.json"))

# short name in routing/dominated tables -> full CLI id. Anything already starting with
# "claude-" or listed as a local model id passes through unchanged.
#
# wq#1329 (ISSUE-LOG #127): this used to be a hand-maintained dict, duplicating
# research/models.yaml's own models[].alias field as a second source of truth. They
# diverged the moment claude-fable-5-1 was added to the manifest without a matching
# entry here: resolve_alias('fable-5-1') fell through unresolved. Now derived from the
# manifest itself so there is exactly one place aliases are declared.
_FALLBACK_ALIASES = {
    # wq#1329: only used if models.yaml is missing/unparseable at import time (keeps the
    # CLI usable for e.g. --help). Once load_manifest() runs successfully this is dead
    # weight but never wrong — every entry here is also derivable from the yaml.
    "fable-5": "claude-fable-5", "fable": "claude-fable-5",
    "opus-5": "claude-opus-5", "opus": "claude-opus-5",
    "opus-5-5": "claude-opus-5-5",   # wq#2450: 'opus' stays pinned to opus-5, not this
    "sonnet-5": "claude-sonnet-5", "sonnet": "claude-sonnet-5",
    "haiku-5-5": "claude-haiku-5-5", "haiku": "claude-haiku-5-5",   # wq#3618
    "haiku-4-5": "claude-haiku-4-5",
    "opus-4-8": "claude-opus-4-8", "opus-4-7": "claude-opus-4-7", "opus-4-6": "claude-opus-4-6",
    "sonnet-4-6": "claude-sonnet-4-6",
}


def _build_aliases(path=None, data=None):
    """alias -> full id, built from research/models.yaml models[].id + models[].alias.
    For every model: its declared `alias` field (if any) maps to its id, and the bare
    family shorthand (id with the 'claude-' prefix stripped, e.g. 'fable-5-1' for
    'claude-fable-5-1') also maps to it — that shorthand rule is what reproduces every
    entry _FALLBACK_ALIASES used to hand-list, with zero maintenance, including the
    fable-5-1 entry that was previously missing (ISSUE-LOG #127).

    wq#4220: `legacy_aliases:` (a list) maps the same way, one rung lower in priority —
    it is the retired-name compatibility field, so a name some other row claims as its
    id or its live `alias` always wins over a legacy claim on the same string. Before
    this, legacy_aliases was read by nothing while wq#4210's commit message claimed it
    was what kept the old deepseek name resolving (wq#4217 CONTRADICT).

    `data` lets a caller that already parsed the yaml (load_manifest) skip a second
    read; passed None (the module-import-time call below), this does its own read."""
    p = path or MANIFEST_PATH
    try:
        if data is None:
            if yaml is None:
                return dict(_FALLBACK_ALIASES)
            with open(p) as f:
                data = yaml.safe_load(f) or {}
        aliases, legacy, ids = {}, {}, set()
        for m in (data or {}).get("models") or []:
            mid = m.get("id")
            if not mid:
                continue
            ids.add(mid)
            alias = m.get("alias")
            if alias:
                aliases[alias] = mid
            if mid.startswith("claude-"):
                aliases[mid[len("claude-"):]] = mid
            for old in m.get("legacy_aliases") or []:
                legacy.setdefault(old, mid)
        for old, mid in legacy.items():
            if old not in aliases and old not in ids:
                aliases[old] = mid
        return aliases or dict(_FALLBACK_ALIASES)
    except (OSError, ValueError):
        return dict(_FALLBACK_ALIASES)


ALIASES = _build_aliases()

# FOREMAN_RESEARCH §15 numbering — dominated rows say "(archetypes 1-2)".
ARCHETYPE_NUMBERS = {
    1: "mechanical-edit", 2: "probe-build", 3: "standard-engineering",
    4: "rca-investigation", 5: "novel-design", 6: "long-horizon-campaign",
    7: "research-synthesis", 8: "verify-pass",
    9: "fire-response",  # wq#717 (AUTO-5): fleet-fires manual-approval workflow
    # design/orchestrator_verify_lane.md §5.2 calls this "archetype 9" — written before
    # wq#717 claimed 9 for fire-response. 10 is the first free slot; the design's intent
    # (a verify-pass sibling with reach, not mutation, grants) is unaffected by the number.
    10: "verify-probe",
}
ARCHETYPES = list(ARCHETYPE_NUMBERS.values())
# routing rows that are not ticket archetypes: the foreman's own judgment sessions.
EXTRA_ROUTING_ROWS = {"orchestrator", "decomposer"}  # wq#2515: planning row for the DECOMPOSE consumer

# Soft-pressure margin: remaining% above the governor floor below which governor_pressure
# reports 'tight'. Above it: 'ok'. Below the floor: 'deny'. 'tight' never changes the
# tuple (doctrine 3) — it is the dispatcher's signal to engage the burn-rate ceiling.
# wq#1669: this is only the DEFAULT — floors.json `soft_margin_pct` overrides it, so
# zeroing floors.json (Frank 2026-08-23) can zero the margin too instead of leaving a
# hardcoded 15-point throttle behind the zeroed floors.
SOFT_MARGIN_PCT = 15


class ManifestError(Exception):
    pass


class Rung:
    """One (model, effort) step in a routing chain. model is a full CLI id, or the
    sentinel 'DECOMPOSE'. effort None = launch without --effort (haiku, local rung).
    pool 'local' = the LiteLLM rung (T4). condition = escalation annotation or None."""
    __slots__ = ("model", "effort", "pool", "condition")

    def __init__(self, model, effort=None, pool="cloud", condition=None):
        self.model, self.effort, self.pool, self.condition = model, effort, pool, condition

    def key(self):
        return (self.model, self.effort)

    def __repr__(self):
        return self.spec()

    def spec(self):
        s = self.model + (f"@{self.effort}" if self.effort else "")
        if self.pool == "local":
            s += "@local"
        if self.condition:
            s += f"({self.condition})"
        return s

    def as_dict(self):
        return {"model": self.model, "effort": self.effort, "pool": self.pool,
                "condition": self.condition}


def resolve_alias(name):
    name = name.strip()
    if name in ALIASES:
        return ALIASES[name]
    return name


_RUNG_RE = re.compile(r"^\s*([A-Za-z0-9._-]+)(?:@([a-z]+))?\s*(?:\(([^)]*)\))?\s*$")


def parse_rung(spec):
    """'sonnet-5@high', 'deepseek-v4-flash@local', 'opus-5@medium(escalate-on-fail)',
    'haiku-4-5', 'DECOMPOSE'."""
    if spec == "DECOMPOSE":
        return Rung("DECOMPOSE", None, "none", None)
    m = _RUNG_RE.match(spec)
    if not m:
        raise ManifestError(f"unparseable rung {spec!r}")
    model, effort, cond = m.groups()
    pool = "cloud"
    if effort == "local":
        pool, effort = "local", None
    elif effort is not None and effort not in EFFORT_LADDER:
        raise ManifestError(f"bad effort {effort!r} in rung {spec!r}")
    return Rung(resolve_alias(model), effort, pool, cond)


class Manifest:
    def __init__(self, data, path=None):
        self.path = path
        self.raw = data
        self.models = {}
        for m in data.get("models", []):
            self.models[m["id"]] = m
        self.routing = {}
        for arch, chain in (data.get("routing") or {}).items():
            self.routing[arch] = [parse_rung(s) for s in chain]
        self.dominated_exact, self.dominated_scoped, self.dominated_families = \
            self._parse_dominated(data.get("dominated_never_select") or [])
        self.refusal = data.get("refusal_fallback") or {}
        self.effort_res = data.get("effort_resolution") or {}
        self.verify_policy = data.get("verify_pass") or {}
        self.judgment_policy = data.get("judgment") or {}
        # wq#2133: flipped after the local tok/s numbers land; false = standard-engineering
        # keeps its cloud-only row
        self.local_standard_engineering = data.get("local_standard_engineering") is True
        # wq#5083: Fable security exclusion
        self.security = self._parse_security(data.get("security_exclusion") or {})
        self.check()

    # ---- dominated ---------------------------------------------------------------
    @staticmethod
    def _parse_dominated(entries):
        exact, scoped, families = set(), {}, []
        for e in entries:
            e = str(e).strip()
            fam = re.match(r"^all (.+?) tuples while 5-gen sibling selectable$", e)
            if fam:
                for tok in re.split(r"\s+and\s+|,\s*", fam.group(1)):
                    families.append(tok.strip())      # 'opus-4-x', 'sonnet-4-6'
                continue
            m = re.match(r"^([A-Za-z0-9._-]+)@([a-z]+)\s*(?:\(archetypes?\s+([0-9,\s-]+)\))?", e)
            if not m:
                raise ManifestError(f"unparseable dominated entry {e!r}")
            model, effort, arch_spec = resolve_alias(m.group(1)), m.group(2), m.group(3)
            if effort not in EFFORT_LADDER:
                raise ManifestError(f"bad effort in dominated entry {e!r}")
            if arch_spec:
                nums = set()
                for part in arch_spec.replace(" ", "").split(","):
                    if "-" in part:
                        a, b = part.split("-")
                        nums.update(range(int(a), int(b) + 1))
                    elif part:
                        nums.add(int(part))
                scoped.setdefault((model, effort), set()).update(
                    ARCHETYPE_NUMBERS[n] for n in nums)
            else:
                exact.add((model, effort))
        return exact, scoped, families

    def _family_dominated(self, model):
        """'all opus-4-x and sonnet-4-6 tuples while 5-gen sibling selectable'."""
        for fam in self.dominated_families:
            pat = "^claude-" + re.escape(fam).replace("x", "[0-9]+") + "$"
            if re.match(pat, model):
                sibling = "claude-opus-5" if fam.startswith("opus") else "claude-sonnet-5"
                sib = self.models.get(sibling) or {}
                if str(sib.get("status", "")).startswith("LIVE"):
                    return f"{model} dominated by {sibling} (LIVE)"
        return None

    def is_dominated(self, model, effort, archetype=None):
        """-> reason string if (model, effort) must never be selected, else None."""
        model = resolve_alias(model)
        if (model, effort) in self.dominated_exact:
            return f"{model}@{effort} is dominated_never_select"
        scope = self.dominated_scoped.get((model, effort))
        if scope and (archetype is None or archetype in scope):
            return f"{model}@{effort} is dominated for archetypes {sorted(scope)}"
        fam = self._family_dominated(model)
        if fam:
            return fam
        return None

    # ---- effort ceilings ---------------------------------------------------------
    def efforts_for(self, model):
        m = self.models.get(model)
        if m is not None and "efforts" in m:
            return list(m["efforts"] or [])
        return list(EFFORT_LADDER)

    def clamp_effort(self, model, effort):
        """CLAUDE_CLI_INTERNALS §A downgrade_rule: unsupported max/xhigh -> high."""
        if effort is None:
            return None
        ok = self.efforts_for(model)
        if not ok:
            return None
        if effort in ok:
            return effort
        return "high" if "high" in ok else ok[-1]

    # ---- routing -----------------------------------------------------------------
    def chain(self, archetype):
        if archetype not in self.routing:
            raise ManifestError(f"no routing row for archetype {archetype!r}")
        rungs = list(self.routing[archetype])
        if (archetype == "standard-engineering" and self.local_standard_engineering
                and not any(r.pool == "local" for r in rungs)):
            rungs.insert(0, Rung(LOCAL_WORKER, None, "local"))
        return rungs

    def refusal_landing(self, model, category):
        """Where the CLI's own refusal-fallback sends a (model, category) refusal.
        None = hard refusal (no fallback)."""
        model = resolve_alias(model)
        table = self.refusal.get("default") or {}
        if model in ("claude-opus-5", "claude-opus-5[1m]"):
            table = self.refusal.get("when_refusing_is_opus5") or {}
        return table.get(category)

    def keep_entitled(self):
        return list(self.refusal.get("keep_entitled") or [])

    def roster(self):
        routed = set()
        for chain in self.routing.values():
            for r in chain:
                routed.add(r.model)
        rows = []
        for mid, m in self.models.items():
            rows.append({
                "id": mid, "status": m.get("status"),
                "routed": mid in routed,
                "keep_entitled": mid in self.keep_entitled(),
                "family_dominated": bool(self._family_dominated(mid)),
            })
        return rows

    # ---- security exclusion (wq#5083) --------------------------------------------
    @staticmethod
    def _parse_security(raw):
        chain = [parse_rung(s) for s in (raw.get("chain") or SECURITY_FALLBACK_CHAIN)]
        return {
            "never_model_prefixes": list(raw.get("never_model_prefixes") or SECURITY_NEVER_PREFIXES),
            "chain": chain,
            "exempt_archetypes": set(raw.get("exempt_archetypes") or ["long-horizon-campaign"]),
            "tags": [str(t) for t in (raw.get("tags") or SECURITY_DEFAULT_TAGS)],
        }

    def never_for_security(self, model):
        """True when `model` may never run a security ticket (every Fable generation)."""
        model = resolve_alias(model or "")
        return any(model.startswith(p) for p in self.security["never_model_prefixes"])

    def security_chain(self, archetype):
        """The chain a security ticket of `archetype` routes on: the archetype's own chain for
        an exempt archetype (DECOMPOSE), else security_exclusion.chain."""
        if archetype in self.security["exempt_archetypes"]:
            return self.chain(archetype)
        return list(self.security["chain"])

    # ---- self-check (the probe's section A) --------------------------------------
    def check(self):
        problems = []
        for arch, chain in self.routing.items():
            if arch not in ARCHETYPES and arch not in EXTRA_ROUTING_ROWS:
                problems.append(f"routing row for unknown archetype {arch!r}")
            for r in chain:
                if r.model == "DECOMPOSE":
                    continue
                why = self.is_dominated(r.model, r.effort, arch)
                if why:
                    problems.append(f"routing[{arch}] contains dominated rung {r}: {why}")
                if r.pool == "cloud" and r.model not in self.models:
                    problems.append(f"routing[{arch}] rung {r} not in models list")
                if r.effort and r.effort not in self.efforts_for(r.model):
                    problems.append(f"routing[{arch}] rung {r}: effort not supported")
        for arch in ARCHETYPES:
            if arch not in self.routing:
                problems.append(f"no routing row for archetype {arch}")
        for mid in self.keep_entitled():
            st = str((self.models.get(mid) or {}).get("status", ""))
            if not st.startswith("LIVE"):
                problems.append(f"keep_entitled {mid} is not LIVE in models list ({st!r})")
        # wq#5083: the security chain itself must be routable and Fable-free
        if not self.security["chain"]:
            problems.append("security_exclusion.chain is empty")
        for r in self.security["chain"]:
            if self.never_for_security(r.model):
                problems.append(f"security_exclusion.chain holds never-for-security rung {r}")
            if r.pool == "cloud" and r.model not in self.models:
                problems.append(f"security_exclusion.chain rung {r} not in models list")
            if r.effort and r.effort not in self.efforts_for(r.model):
                problems.append(f"security_exclusion.chain rung {r}: effort not supported")
            why = self.is_dominated(r.model, r.effort)
            if why:
                problems.append(f"security_exclusion.chain contains dominated rung {r}: {why}")
        for arch in self.security["exempt_archetypes"]:
            if any(r.model != "DECOMPOSE" and self.never_for_security(r.model)
                   for r in self.routing.get(arch, [])):
                problems.append(f"security exempt archetype {arch} routes a never-for-security model")
        if problems:
            raise ManifestError("; ".join(problems))
        return True


# ---- security exclusion (wq#5083) ------------------------------------------------------
# Fallbacks only: models.yaml security_exclusion is the source of truth.
SECURITY_NEVER_PREFIXES = ("claude-fable-",)
SECURITY_FALLBACK_CHAIN = ("opus-4-6@high", "opus-5-5@high")
SECURITY_DEFAULT_TAGS = ("RE", "miko", "exploit", "firmware", "security", "sec", "mtkclient",
                         "edl", "flashrom", "ghidra", "mt8167", "avb", "bootloader",
                         "questfree", "reverse engineer")
# tags matched ONLY as a bracket tag / lane / subsystem token, never as a prose word
_SECURITY_TOKEN_ONLY = {"sec"}
# tags that must match a WHOLE word/token. Every other single-word tag is a PREFIX match
# (wq#5083 follow-up: 'Miko3', 'MikoUnchained', 'exploits', 'firmwares' slipped a
# whole-word match and left 3 open Miko tickets stamped Fable).
_SECURITY_WHOLE_WORD = {"re", "sec", "edl"}
_SEC_SPLIT_RE = re.compile(r"[-_,\s/:.]+")


def _sec_tokens(*texts):
    out = set()
    for t in texts:
        out.update(x for x in _SEC_SPLIT_RE.split((t or "").lower()) if x)
    return out


def is_security_ticket(t, manifest=None):
    """wq#5083 -> [matched signals] (empty = not a security ticket). `t` is a wq ticket dict
    (title/detail/lane/subsystems). Over-matching is the safe direction: a false positive costs
    an Opus run instead of a Fable/Sonnet one; a false negative puts RE work on Fable."""
    manifest = manifest or load_manifest()
    title, detail = t.get("title") or "", t.get("detail") or ""
    lane, subs = t.get("lane") or "", t.get("subsystems") or ""
    tag_tokens = _sec_tokens(*_TAG_RE.findall(title), lane, subs)
    hits = []
    for tag in manifest.security["tags"]:
        low = tag.lower()
        if " " not in low and (low in tag_tokens or (
                low not in _SECURITY_WHOLE_WORD and any(tok.startswith(low) for tok in tag_tokens))):
            hits.append(f"tag/lane/subsystem {tag}")
            continue
        if low in _SECURITY_TOKEN_ONLY:
            continue
        if tag == "RE":   # case-sensitive: 're-run', 'regex', 'Re:' must not match
            pat = re.compile(r"(?<![\w-])RE(?![\w-])")
        elif " " in low:  # phrase, prefix match ('reverse engineer' covers -ed/-ing)
            pat = re.compile(r"(?<!\w)" + r"[\s-]+".join(map(re.escape, low.split())), re.IGNORECASE)
        elif low in _SECURITY_WHOLE_WORD:
            pat = re.compile(r"(?<!\w)" + re.escape(low) + r"(?!\w)", re.IGNORECASE)
        else:             # prefix match: Miko3, MikoUnchained, exploits, firmwares
            pat = re.compile(r"(?<!\w)" + re.escape(low), re.IGNORECASE)
        if pat.search(title):
            hits.append(f"title word {tag}")
        elif pat.search(detail):
            hits.append(f"detail word {tag}")
    return hits


def security_guard(ticket, model, effort=None, manifest=None):
    """wq#5083 last line: -> None when (model, effort) may run `ticket`, else
    (new_model, new_effort, reason). Cheap no-op for any non-Fable model (no wq call).
    `ticket` is an id or a ticket dict. Fail-closed: if the ticket cannot be read, a Fable
    model is still swapped (unknown content is treated as security content)."""
    manifest = manifest or load_manifest()
    if not model or not manifest.never_for_security(model):
        return None
    try:
        t = ticket if isinstance(ticket, dict) else wq_show(ticket)
        hits = is_security_ticket(t, manifest)
        arch = _stored_archetype(t) or "standard-engineering"
    except SystemExit:
        hits, arch = ["ticket unreadable (fail-closed)"], "standard-engineering"
    if not hits:
        return None
    rung, _ = select(manifest, arch, security=True)
    if rung is None or rung.model == "DECOMPOSE":
        rung = manifest.security["chain"][0]
    return rung.model, manifest.clamp_effort(rung.model, rung.effort), \
        f"security_exclusion: {resolve_alias(model)} never runs security tickets ({'; '.join(hits[:3])})"


_MANIFEST = None


def load_manifest(path=None, force=False):
    global _MANIFEST, ALIASES
    if _MANIFEST is not None and not force and path is None:
        return _MANIFEST
    p = path or MANIFEST_PATH
    if yaml is None:
        raise ManifestError("PyYAML not installed")
    with open(p) as f:
        data = yaml.safe_load(f)
    # wq#1329: keep resolve_alias() in sync with whichever manifest is actually being
    # loaded — matters for --manifest / FOREMAN_MODELS_YAML overrides and tests, where
    # the import-time ALIASES (built from the default MANIFEST_PATH) would otherwise be
    # stale for a rung this manifest's own parse_rung() call below is about to resolve.
    ALIASES = _build_aliases(p, data=data)
    m = Manifest(data, p)
    if path is None:
        _MANIFEST = m
    return m


# ---- selection ----------------------------------------------------------------------

def _condition_met(cond, fails, cross_subsystem, contradiction):
    if not cond:
        return True
    c = cond.lower()
    if "escalate-on-fail" in c:
        return fails >= 1
    if "2-fails" in c:
        return fails >= 2 or (cross_subsystem and "cross-subsystem" in c)
    if "on-contradiction" in c:
        return bool(contradiction)
    if "cal-1" in c:
        return False          # rungs parked until calibration
    return True


def _local_group_members(name):
    """Member backends of the LiteLLM group `name` per config.json `local_backends`, else [].
    Unreadable config = no group known: the rung is then judged by its own name only."""
    try:
        with open(DISPATCHER_CONFIG) as f:
            row = ((json.load(f) or {}).get("local_backends") or {}).get(name)
    except (OSError, ValueError, AttributeError):
        return []
    members = row.get("group") if isinstance(row, dict) else None
    return [m for m in members if isinstance(m, str)] if isinstance(members, list) else []


def availability_check(manifest, now=None):
    """wq#2133: -> f(rung) -> None when the rung can be picked, else the reason it cannot.
    A rung is out when its model's models.yaml status is not LIVE*, or when its model is
    present and unexpired in runtime/backend_cooldown.json (any pool since wq#3354:
    fleet-selfheal cools a cloud rung whose provider key is dead). The `local-worker` group
    is out only when every member is cooling down or not LIVE*. A model with no models.yaml
    row is judged by cooldown alone. Rollback: FOREMAN_LOCAL_CAPACITY=static disables it.
    wq#2527: a claude-* rung is also out while cli_canary says the installed CLI cannot drive
    its model (FOREMAN_CLI_CANARY_MODE=off is that half's own rollback)."""
    canary = cli_canary.gate(manifest.models)
    if local_capacity.capacity_mode() == "static":
        return lambda rung: None if rung.model == "DECOMPOSE" else canary(rung.model)
    cooled = local_capacity.cooled_down_backends(now)

    def status(model):
        return (manifest.models.get(model) or {}).get("status")

    def live(model):
        st = status(model)
        return st is None or str(st).startswith("LIVE")

    def check(rung):
        if rung.model == "DECOMPOSE":
            return None
        if not live(rung.model):
            return f"{rung.model} status is {status(rung.model)!r}, not LIVE*"
        if rung.model in cooled:
            return f"{rung.model} is cooling down (backend_cooldown.json)"
        if rung.pool != "local":
            return canary(rung.model)
        members = _local_group_members(rung.model)
        if members:
            why = {m: ("cooling down" if m in cooled else f"status {status(m)!r}")
                   for m in members if m in cooled or not live(m)}
            if len(why) == len(members):
                return f"every {rung.model} member is out: " + ", ".join(
                    f"{m} {w}" for m, w in why.items())
        return None
    return check


def candidates(manifest, archetype, fails=0, cross_subsystem=False, contradiction=False,
               available_only=True, security=False):
    """The eligible chain for this ticket's facts, in preference order, dominated rungs
    removed (defensively; check() already refuses manifests that contain them). With
    available_only (wq#2133) rungs failing availability_check() are removed too; select()
    passes False and applies the check itself, after the attempt-count walk, so a cooled-down
    rung still counts as a position in the chain."""
    usable = availability_check(manifest) if available_only else (lambda rung: None)
    out = []
    chain = manifest.security_chain(archetype) if security else manifest.chain(archetype)
    for r in chain:
        if not _condition_met(r.condition, fails, cross_subsystem, contradiction):
            continue
        if security and r.model != "DECOMPOSE" and manifest.never_for_security(r.model):
            continue   # wq#5083
        if r.model != "DECOMPOSE" and manifest.is_dominated(r.model, r.effort, archetype):
            continue
        if usable(r):
            continue
        out.append(r)
    return out


def select(manifest, archetype, *, pressure=None, fails=0, cross_subsystem=False,
           contradiction=False, allow_local=True, security=False):
    """Pick the (model, effort) for one dispatch.

    pressure(model) -> 'ok' | 'tight' | 'deny'   (default: everything 'ok')
      tight = soft budget pressure -> NO effect here; the dispatcher answers it with the
              burn-rate concurrency ceiling instead (doctrine 3, wq#1723)
      deny  = governor refuses that model -> next model in the chain
    A rung whose model status is not LIVE*, or whose local backend is cooling down, is
    skipped the same way (availability_check, wq#2133).
    Escalation facts (fails, cross_subsystem, contradiction) unlock annotated rungs; when
    any escalation rung is unlocked it is preferred over the primary (that is what
    escalation means), still subject to pressure.

    Returns (Rung, [reasons]) or (None, [reasons]) if nothing is dispatchable.
    """
    pressure = pressure or (lambda m: "ok")
    reasons = []
    cands = candidates(manifest, archetype, fails, cross_subsystem, contradiction,
                       available_only=False, security=security)
    unavailable = availability_check(manifest)
    if security:
        reasons.append("security_exclusion: Fable never selected; security chain in use (wq#5083)")
    if not cands:
        return None, [f"no eligible rung for {archetype}"]
    if cands[0].model == "DECOMPOSE":
        return cands[0], ["archetype requires decomposition; never one worker"]

    # escalation rungs that are unlocked jump to the front (highest-condition first)
    esc = [r for r in cands if r.condition]
    base = [r for r in cands if not r.condition]
    # wq#779 F3: the un-annotated (non-escalation) part of the chain used to move only on
    # governor pressure, so a repeatedly-failing ticket kept re-hitting the same primary
    # rung forever whenever pressure stayed 'ok' (which it did: saw_limit 0/149). Each
    # recorded fail now walks `base` forward by one rung, triggered by attempt count. This
    # is the ONLY in-router downgrade left (wq#1723 deleted the pressure-driven effort-first
    # one, which measured as a wash on spend and worse on rework). Never walks past the
    # last rung (that floor stays reachable), and never touches `esc`: an escalation
    # annotation's own condition (escalate-on-fail, 2-fails, ...) already governs when that
    # rung unlocks and takes precedence over base once it does.
    if fails > 0 and len(base) > 1:
        skip = min(fails, len(base) - 1)
        reasons.append(f"attempt-count downgrade: fails={fails} skips {skip} rung(s) of {archetype}'s base chain")
        base = base[skip:]
    ordered = list(reversed(esc)) + base if esc else base
    if esc:
        reasons.append(f"escalation unlocked: {[r.spec() for r in esc]}")

    seen = set()
    for r in ordered:
        if r.pool == "local" and not allow_local:
            reasons.append(f"skip {r}: local rung not allowed here")
            continue
        why = unavailable(r)
        if why:
            reasons.append(f"skip {r}: {why}")
            continue
        if security and manifest.never_for_security(r.model):   # wq#5083 belt-and-braces
            reasons.append(f"skip {r}: security_exclusion")
            continue
        p = pressure(r.model) if r.pool == "cloud" else "ok"
        if p == "deny":
            reasons.append(f"skip {r}: governor denies {r.model}")
            continue
        model, effort = r.model, r.effort
        if p == "tight":
            # wq#1723: 'tight' used to step effort down one rung here. It no longer does —
            # see doctrine 3. The reason line stays so a dispatch pass still records that
            # the governor was soft-pressured when this tuple was picked.
            reasons.append(f"{model}: soft pressure noted; burn-rate ceiling answers it, tuple unchanged")
        effort = manifest.clamp_effort(model, effort)
        why = manifest.is_dominated(model, effort, archetype)
        if why:            # a manifest chain should never hold one, but never emit it
            reasons.append(f"skip {model}@{effort}: {why}")
            continue
        if (model, effort) in seen:
            continue
        seen.add((model, effort))
        chosen = Rung(model, effort, r.pool, r.condition)
        reasons.append(f"selected {chosen.spec()}")
        return chosen, reasons
    reasons.append("every rung denied, dominated or unavailable")
    return None, reasons


# ---- archetype classification (FOREMAN_RESEARCH §15 rubric) --------------------------

_RUBRIC = [
    # (archetype, [regex signals]) — evaluated in this order; first hit wins unless a later
    # stronger signal is listed as an override below.
    ("verify-pass", [r"\bverify[- ]pass\b", r"\bfresh[- ]eyes\b", r"\badversarial\b",
                     r"\bre-?check\b", r"\beval gate\b", r"\baudit of (the )?fix\b"]),
    ("long-horizon-campaign", [r"\bcampaign\b", r"\bmulti-session\b", r"\bcheckpoint",
                               r"\buplift\b", r"\bpivot\b", r"\bchain\b"]),
    # wq#2615: the design signal must not fire on a design-doc CITATION — nine MCP-HA
    # provisioning tickets whose detail opened "Design /home/.../MCP_CONTROL_PLANE_HA_
    # DESIGN.md §3.2" all routed novel-design -> fable-5@high off that one reference.
    # CLASS: keyword classifiers fire on references, not intent. The guards drop a match
    # that is part of a path/filename (DESIGN.md, docs/design/...) or takes a path
    # argument ("design[ doc] /path/..."); prose intent ("Design the replacement pacing
    # curve", wq#1669) still counts, from title OR detail — a title-only gate here would
    # re-misroute wq#1669-shaped tickets to mechanical-edit.
    ("novel-design", [r"(?<!/)\bdesign( doc)?\b(?!\.md)(?!/)(?!( doc)?\s+\S*/)",
                      r"\bnew (service|subsystem|daemon)\b",
                      r"\barchitect", r"\binterface decision", r"\bwiring\b.*\bnew\b"]),
    ("rca-investigation", [r"\baudit\b", r"\bwhy\b", r"\bbypass", r"\bincident\b",
                           r"\broot cause\b", r"\brca\b", r"\binvestigat", r"\bescape\b",
                           r"\bdup(licate)?\b", r"\bleak\b", r"\bflap"]),
    ("research-synthesis", [r"\bresearch\b", r"\bsynthes", r"\breport\b", r"\bsurvey\b",
                            r"\bevidence\b", r"\bknowledge base\b", r"\bliterature\b"]),
    ("probe-build", [r"\bprobe\b", r"\bCI\b", r"\bmeasure\b", r"\bbenchmark\b",
                     r"\btest harness\b", r"\bhygiene\b"]),
    ("mechanical-edit", [r"\bwire\b", r"\brelabel\b", r"\brename\b",
                         r"\bgenerate[- ]from[- ]source\b", r"\bone-?liner\b",
                         r"\bsingle[- ]file\b", r"\bbump\b", r"\btypo\b"]),
]
_LANE_BIAS = {
    "pokemon": ["rca-investigation", "long-horizon-campaign"],
    "memory": ["novel-design", "research-synthesis"],
    "fleet": ["mechanical-edit", "standard-engineering"],
    "research": ["research-synthesis"],
    # wq#1559: a pilot ticket is two jobs in one — RUN the experiment, then WRITE it
    # up. probe-build leads because the run is the expensive, fallible half (build a
    # treatment, measure a baseline, hit a kill criterion); research-synthesis is the
    # tie-break for the write-up half. Order is the bias order, not a sequence.
    "pilot": ["probe-build", "research-synthesis"],
}

# wq#778 (from wq#750 audit F1/F2): these four bare-word patterns alone routed 66% of
# research-synthesis and 48% of rca-investigation — both archetypes whose PRIMARY rung is
# opus-5 — off a single common word that shows up in ordinary ticket prose ('research',
# 'report', 'audit', 'why'). An archetype in _OPUS_PRIMARY_ARCHETYPES may not win on one of
# its _WEAK_SIGNALS alone: it needs a second matched pattern (weak or not) or one non-weak,
# phrase-level pattern from its own rubric row.
_OPUS_PRIMARY_ARCHETYPES = {"research-synthesis", "rca-investigation"}
_WEAK_SIGNALS = {
    ("research-synthesis", r"\bresearch\b"),
    ("research-synthesis", r"\breport\b"),
    ("rca-investigation", r"\baudit\b"),
    ("rca-investigation", r"\bwhy\b"),
    # wq#992: long-horizon-campaign -> DECOMPOSE is the most consequential routing the
    # classifier emits (one ticket becomes many). Its one generic signal, \bchain\b,
    # shows up in ordinary prose ("chain tickets", "routing chain", "tool-chain") and
    # must never force decomposition on its own. Same class as the wq#778 bare-word
    # fix, extended to the DECOMPOSE-primary archetype. The other campaign signals
    # (campaign/multi-session/checkpoint/uplift/pivot) stay strong and win alone.
    ("long-horizon-campaign", r"\bchain\b"),
    # wq#1707: same class as \bchain\b, found by replaying the real queue — wq#1628 ("LPP
    # full behavioral analysis from run logs") matched \bcheckpoint and NOTHING else for
    # long-horizon-campaign, out-ranked a 2-signal research-synthesis on _RUBRIC order, and
    # was DECOMPOSEd until Frank hand-corrected it. wq#1143 already documents that
    # checkpoint is save-state vocabulary in LPP prose, but its fix only covers titles
    # prefixed [lpp][stuck]; wq#1628 carried no such prefix. A real campaign says so with
    # campaign/multi-session/uplift/pivot, all of which stay strong and win alone.
    ("long-horizon-campaign", r"\bcheckpoint"),
}

# Archetypes whose routing is expensive/consequential enough that a single WEAK signal
# must not win them alone: the opus-primary pair (wq#778) plus long-horizon-campaign,
# whose primary rung is DECOMPOSE (wq#992).
_CORROBORATION_ARCHETYPES = _OPUS_PRIMARY_ARCHETYPES | {"long-horizon-campaign"}

# wq#1725: same bare-word-in-prose failure class as wq#778/wq#992, but for probe-build's
# generic single-word signals. router.py assign 1724 classified a wq-tooling + audit
# ticket as probe-build because its DETAIL happened to say "a named live probe" (signal
# \bprobe\b), routing an analysis ticket to deepseek-v4-flash. Unlike the corroboration
# gate above (any second signal from anywhere in title+detail rescues the archetype),
# these words (probe/CI/measure/benchmark/hygiene) are plain enough that a mention in
# ticket DETAIL prose proves nothing about the ticket's own archetype — only a TITLE
# match counts. \btest harness\b stays a detail-eligible phrase: two-word phrases are
# specific enough not to show up as incidental prose.
_TITLE_ONLY_SIGNALS = {
    ("probe-build", r"\bprobe\b"),
    ("probe-build", r"\bCI\b"),
    ("probe-build", r"\bmeasure\b"),
    ("probe-build", r"\bbenchmark\b"),
    ("probe-build", r"\bhygiene\b"),
}

# wq#1707: the analysis tier — archetypes whose deliverable is judgment (a design, a root
# cause, a synthesis) rather than a diff. mechanical-edit's primary rung is qwen36-local, a
# small local model chosen for bounded one-line edits; it cannot produce an analysis. So a
# ticket that evidences ANY analysis archetype is never a mechanical edit, however many
# mechanical-sounding words ("wire", "rename", "bump") its prose also happens to contain.
_ANALYSIS_ARCHETYPES = {"novel-design", "rca-investigation", "research-synthesis"}


def _qualifies(arch, matched_pats):
    """wq#778 + wq#992 corroboration gate — see _WEAK_SIGNALS above."""
    if arch not in _CORROBORATION_ARCHETYPES:
        return True
    if len(matched_pats) >= 2:
        return True
    return any((arch, p) not in _WEAK_SIGNALS for p in matched_pats)


# wq#779 F3: the two archetypes the audit measured as structurally outranked — sitting
# last in _RUBRIC order behind every expensive archetype, so any co-occurring expensive
# signal beat them regardless of how well-evidenced the cheap one was. Deliberately NOT a
# blanket price sort across all 8 archetypes: comparing fable-5/opus-5/DECOMPOSE costs
# against each other reshuffles the already-tuned wq#778 balance among the expensive tier
# (confirmed by replaying real queue.db traffic against that change) without fixing
# anything the audit actually flagged, and the audit explicitly warns that pure
# cheapest-wins would misroute genuinely hard RCA work.
_CHEAP_ARCHETYPES = {"mechanical-edit", "probe-build"}


def _cheap_archetype_allowed(qualifying):
    """wq#1707 + wq#1755: a cheap archetype (mechanical-edit, probe-build) may not win a
    _LANE_BIAS tie-break while an analysis archetype also qualifies — see
    _ANALYSIS_ARCHETYPES. wq#1707 guarded _LANE_BIAS['fleet'], which lists mechanical-edit
    first, and the wq#779 F3 cost tie-break (still restricted to mechanical-edit there: see
    the call site). wq#1755 extends the same _LANE_BIAS-loop guard to _LANE_BIAS['pilot'],
    which lists probe-build first and could otherwise put a corroborated analysis ticket
    (e.g. "design doc for pilot benchmark harness") on sonnet-5 instead of fable-5/opus-5.
    A cheap archetype is untouched when it is the only thing the text evidences."""
    return not any(a in _ANALYSIS_ARCHETYPES for a in qualifying)


def _primary_rung_cost(manifest, archetype):
    """Rough relative $ weight of `archetype`'s primary rung: api out-price x effort
    ladder position. Used only to rank the _CHEAP_ARCHETYPES against each other in
    classify_archetype's tie-break (wq#779 F3) — never for dispatch, which always uses the
    manifest chain/pressure/fails machinery in select() above."""
    primary = manifest.chain(archetype)[0]
    price = ((manifest.models.get(primary.model) or {}).get("api_price_mtok") or {}).get("out")
    if price is None:
        price = 1                      # e.g. haiku: no published api price, cheapest cloud tier
    weight = EFFORT_LADDER.index(primary.effort) + 1 if primary.effort in EFFORT_LADDER else 1
    return price * weight


# wq#4633: research-brief shape. The title prefix forces research-synthesis outright; the
# two detail shapes below are strong (non-weak) research-synthesis signals, so a brief
# filed without the prefix still clears the wq#778 gate and, as an analysis archetype,
# shuts mechanical-edit/probe-build out of the tie-break (wq#1707). Detail LENGTH alone is
# deliberately not a signal: long build tickets with pasted logs are common.
_RESEARCH_TITLE_RE = re.compile(r"\s*(research|audit)\s*:", re.IGNORECASE)
_BRIEF_LABEL_RE = re.compile(r"(?:^|[.\n])\s*(research|questions?|research questions)\s*:",
                             re.IGNORECASE)
BRIEF_QUESTION_MIN = 5


def _research_brief_signals(detail):
    """Signals that `detail` is a research brief: a 'Research:'/'Questions:' section label,
    or >= BRIEF_QUESTION_MIN question marks (a question list)."""
    detail = detail or ""
    sig = []
    if _BRIEF_LABEL_RE.search(detail):
        sig.append("brief label Research:/Questions: (wq#4633)")
    if detail.count("?") >= BRIEF_QUESTION_MIN:
        sig.append(f">={BRIEF_QUESTION_MIN} questions in detail (wq#4633)")
    return sig


def classify_archetype(title, detail="", lane="", manifest=None):
    """Keyword rubric from §15: title/detail signals + lane bias, gated by the wq#778
    corroboration rule for opus-primary archetypes, with cost-aware tie-breaking (wq#779
    F3) so mechanical-edit/probe-build are reachable against an equally- or
    less-evidenced pricier archetype. Returns (archetype, [matched signals]). Default
    standard-engineering (archetype 3).

    wq#717 (AUTO-5): fleet-fires is forced to fire-response unconditionally, ahead of
    the keyword rubric (and the wq#778 gate below) — a title that happens to match
    another archetype's pattern (e.g. "audit") must never route a production-state
    ticket around the manual-approval workflow (harness/FIRE_MODE.md)."""
    if lane == "fleet-fires":
        return "fire-response", ["lane:fleet-fires (forced, wq#717)"]
    # wq#1143: LPP stuck-supervisor alerts ([lpp][stuck] ...) are single-worker RCA of one
    # frozen game state (like wq#1097 FIGHT_LOSS_LOOP -> PR#82), never a multi-worker
    # campaign. Force rca-investigation ahead of the keyword rubric, same shape as the
    # fleet-fires override above: these alerts are full of game save-state terms
    # ("checkpoint", "alternate_checkpoint", "checkpoint_restore") that the rubric reads as
    # STRONG long-horizon-campaign signals and mis-routes to DECOMPOSE, parking them
    # undispatched. WARP_OSC/RESUME_DEADEND carry "checkpoint"; FIGHT_LOSS_LOOP does not,
    # which is why only the former mis-routed.
    if (title or "").lstrip().lower().startswith("[lpp][stuck]"):
        return "rca-investigation", ["lpp-stuck alert (forced rca-investigation, wq#1143)"]
    # wq#4633: a RESEARCH:/AUDIT: title prefix is the filer declaring the deliverable is a
    # research doc. wq#4628/wq#4630 ("RESEARCH: Trigger.dev ...", "RESEARCH: LangGraph ...")
    # matched only the weak \bresearch\b (disqualified by wq#778) plus a "how to wire into
    # our dispatch" \bwire\b in the brief, and shipped as mechanical-edit@low. The prefix
    # pins the analysis tier, lane-independent (fleet-fires stays first): the rubric's pick
    # is kept only if it is itself an analysis archetype (an "AUDIT: why ... bypass" stays
    # rca-investigation), anything else becomes research-synthesis.
    m = _RESEARCH_TITLE_RE.match(title or "")
    if m:
        forced = f"title prefix {m.group(1).upper()}: (wq#4633)"
        arch, signals = classify_archetype(title[m.end():], detail, lane, manifest)
        if arch in _ANALYSIS_ARCHETYPES:
            return arch, [forced] + signals
        return "research-synthesis", [forced]
    text = f"{title or ''}\n{detail or ''}"
    by_arch = {}
    for arch, pats in _RUBRIC:
        matched = []
        for p in pats:
            if not re.search(p, text, re.IGNORECASE):
                continue
            if (arch, p) in _TITLE_ONLY_SIGNALS and not re.search(p, title or "", re.IGNORECASE):
                continue    # wq#1725: detail-only hit on a title-only signal doesn't count
            matched.append(p)
        if matched:
            by_arch[arch] = matched
    brief = _research_brief_signals(detail)
    if brief:
        by_arch.setdefault("research-synthesis", []).extend(brief)
    # first-listed archetype in _RUBRIC order that hit at all (used for lane bias + fallback
    # signal reporting); qualifying narrows that to archs that clear the wq#778 gate.
    hit_archs = [a for a, _ in _RUBRIC if a in by_arch]
    if not hit_archs:
        bias = _LANE_BIAS.get(lane or "", [])
        return (bias[0] if bias and bias[0] != "long-horizon-campaign" else
                "standard-engineering"), []
    qualifying = [a for a in hit_archs if _qualifies(a, by_arch[a])]
    if not qualifying:
        # signal(s) fired but none cleared the wq#778 corroboration gate — do not let lane
        # bias resurrect a disqualified archetype (e.g. lane "research" re-picking a
        # disqualified research-synthesis); fall through to the plain default instead, but
        # surface the disqualified pattern(s) rather than claiming true no-signal.
        return "standard-engineering", [p for a in hit_archs for p in by_arch[a]]
    cheap_ok = _cheap_archetype_allowed(qualifying)      # wq#1707 + wq#1755
    lane_bias = _LANE_BIAS.get(lane or "", [])
    # lane bias breaks ties among qualifying hit archs
    for b in lane_bias:
        if b in qualifying and (b not in _CHEAP_ARCHETYPES or cheap_ok):
            return b, by_arch[b]
    incumbent = qualifying[0]
    if incumbent in _CHEAP_ARCHETYPES or len(qualifying) == 1:
        return incumbent, by_arch[incumbent]
    # wq#779 F3: cost-aware resolution, not list-order-first-wins — restricted to the two
    # structurally-outranked archetypes above. A cheap archetype whose evidence is at
    # least as strong as the incumbent's should not lose just because it sits lower in
    # _RUBRIC; among multiple eligible cheap archetypes the cheaper primary rung wins.
    # wq#1755: for a lane whose _LANE_BIAS entry leads with probe-build (pilot), this
    # tie-break is the second path back to it after the loop above turns it away, same
    # shape as the mechanical-edit/fleet case wq#1707 already guards here — so probe-build
    # gets the same cheap_ok gate ONLY in a lane that actually biases toward it. A bare
    # (lane-less) tie-break, like the "build"-lane probe-build-vs-rca-investigation case
    # above, is untouched: that ambiguity is not lane bias resurrecting a rejected pick.
    incumbent_n = len(by_arch[incumbent])
    cheap = [a for a in qualifying if a in _CHEAP_ARCHETYPES and len(by_arch[a]) >= incumbent_n
             and (a != "mechanical-edit" or cheap_ok)
             and (a != "probe-build" or cheap_ok or "probe-build" not in lane_bias)]
    if not cheap:
        return incumbent, by_arch[incumbent]
    manifest = manifest or load_manifest()
    best = min(cheap, key=lambda a: _primary_rung_cost(manifest, a))
    return best, by_arch[best]


# ---- governor pressure adapter ------------------------------------------------------

def governor_pressure(model, *, state_path=STATE_JSON, governor_cli=GOVERNOR_CLI,
                      soft_margin=SOFT_MARGIN_PCT):
    """'deny' if `foreman_usaged.py can-dispatch MODEL` refuses; else 'tight' when any pool
    the model draws from is within the soft margin of its floor, flagged critical by the
    usage API, or flagged warning on a floored pool; else 'ok'. Fail-closed: unreadable state =
    'deny'.

    wq#1669: floors.json `soft_margin_pct` overrides the soft_margin argument. The API
    flags warning around 80% used, which throttled the whole fleet during a
    burn-before-reset window despite Frank zeroing every floor.
    wq#1699: severity is graded — warning is advisory on a zero-floor pool, critical
    returns tight on any pool. 'tight' no longer means a flat clamp: the dispatcher
    turns it into a burn-rate concurrency ceiling (dispatcher/burn_rate.py) and logs
    the raw labels once per pass (api_severity)."""
    try:
        p = subprocess.run([sys.executable, governor_cli, "can-dispatch", model],
                           capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            return "deny"
    except (OSError, subprocess.TimeoutExpired):
        return "deny"
    if concurrency.is_local_model(model):
        # wq#2081: can-dispatch already passed (STOP + stale gates). The Anthropic pools
        # below meter quota a local rung never touches (models.yaml pools:[local],
        # governor-exempt by design) — reading them would throttle free compute.
        return "ok"
    try:
        with open(state_path) as f:
            st = json.load(f)
        floors_path = os.path.join(os.path.dirname(governor_cli), "floors.json")
        floors = {}
        if os.path.exists(floors_path):
            with open(floors_path) as f:
                floors = json.load(f)
    except (OSError, ValueError):
        return "deny"
    limits = st.get("limits") or {}
    soft_margin = floors.get("soft_margin_pct", soft_margin)
    checks = [("session", floors.get("session_min_remaining_pct", 20)),
              ("weekly_all", floors.get("weekly_all_min_remaining_pct", 15))]
    if "fable" in model:
        checks.append(("fable_weekly_scoped", floors.get("fable_weekly_min_remaining_pct", 50)))
    for pool, floor in checks:
        b = limits.get(pool) or {}
        if b.get("percent") is None:
            # wq#1737: a bucket the poll never returned has no authority on a
            # zero-floor pool (same rule as governor_core's stale branch).
            if floor > 0:
                return "deny"
            continue
        remaining = 100 - b["percent"]
        if remaining < floor:
            return "deny"
        if remaining - floor < soft_margin:
            return "tight"
        # wq#1699: warning != critical. critical always engages the dispatcher's
        # burn-rate controller (a scaled ceiling, never a flat clamp); warning (~80%
        # used) only does when Frank has set a floor on that pool.
        sev = b.get("severity")
        if sev == "critical" or (floor > 0 and sev == "warning"):
            return "tight"
    return "ok"


# ---- wq glue -------------------------------------------------------------------------

WQ_TIMEOUT_S = 30  # wq#1408: an sqlite-lock hang used to wedge a whole dispatch pass
# with no pass_end and no error line — TimeoutStartSec=600 eventually SIGTERMed the
# unit, but nothing in dispatcher.log ever said why. Callers decide what a timeout
# means for their ticket (foreman_dispatcher.py's primary wq_show call treats it the
# same as "no such ticket" — vanished).


def wq(*args):
    return subprocess.run([WQ_BIN, *args], capture_output=True, text=True,
                          timeout=WQ_TIMEOUT_S)


def wq_show(ticket_id):
    """wq#1496: plain-text `wq show` escapes embedded newlines as literal backslash-n
    (wq#1277), so a line-oriented parser here handed every MULTILINE-anchored regex a
    detail/notes field with no real newlines to anchor on (verify_repo_hint,
    repo_sweep.repo_hint, needs_repo_hint all read t.get('detail'/'notes') from this
    dict). `wq show --json` (wq#1277) round-trips real newlines and native column
    types via json.loads; use it instead of parsing the plain-text mode."""
    p = wq("show", str(ticket_id), "--json")
    try:
        out = json.loads(p.stdout)
    except ValueError:
        raise SystemExit(f"router: no such ticket {ticket_id}")
    if not isinstance(out, dict) or out.get("error") or out.get("id") is None:
        raise SystemExit(f"router: no such ticket {ticket_id}")
    return out


def _stored_archetype(t):
    a = t.get("archetype")
    return a if a not in (None, "", "None") else None


def prefix_overrides_stored(t):
    """wq#4716: a ticket stamped at filing (wq#4183: standard-engineering) and LATER
    retitled RESEARCH:/AUDIT: kept its stale stamp — assign() trusted the stored column
    and never reached classify_archetype's wq#4633 prefix pin. The prefix wins over a
    stored non-analysis archetype; a stored analysis archetype stays (the pin keeps those)."""
    stored = _stored_archetype(t)
    return bool(stored and stored not in _ANALYSIS_ARCHETYPES
                and _RESEARCH_TITLE_RE.match(t.get("title") or ""))


def assign(ticket_id, *, archetype=None, manifest=None, pressure=None, dry_run=False,
           fails=0, cross_subsystem=False):
    """Classify (unless told), select, and write (model, effort, archetype) into the
    ticket. Returns the decision dict."""
    manifest = manifest or load_manifest()
    t = wq_show(ticket_id)
    arch = archetype or (None if prefix_overrides_stored(t) else _stored_archetype(t))
    signals = []
    if not arch:
        arch, signals = classify_archetype(t.get("title", ""), t.get("detail", ""), t.get("lane", ""), manifest)
    subs = [s for s in (t.get("subsystems") or "").split(",") if s]
    cross = cross_subsystem or len(subs) > 1
    sec = is_security_ticket(t, manifest)   # wq#5083
    rung, reasons = select(manifest, arch, pressure=pressure, fails=fails, cross_subsystem=cross,
                           security=bool(sec))
    decision = {"ticket": int(ticket_id), "archetype": arch, "signals": signals,
                "security": sec,
                "rung": rung.as_dict() if rung else None, "reasons": reasons,
                "written": False}
    if rung is None or rung.model == "DECOMPOSE" or dry_run:
        return decision
    p = wq("edit", str(ticket_id), "--model", rung.model, "--effort", rung.effort or "",
           "--archetype", arch)
    decision["written"] = p.returncode == 0 and "edited" in p.stdout
    decision["wq"] = (p.stdout + p.stderr).strip()
    return decision


# wq#2581: mirrors harness/fworker.py's LOCAL_POOL_MODELS/LOCAL_RUNG_MODELS, deliberately.
# foreman_dispatcher.py (escalate_off_local's other caller) has no import path to a worker
# script, and this codebase already keeps several independently hand-tuned copies of
# "which models count as local" (fworker.py's own comment names governor/governor_core.py's
# and orchestrator/judgment.py's as two more, each deliberately different -- e.g. fworker's
# excludes deepseek-v4-flash as cheap_cloud, not local-rung). Rather than force one shared
# list on consumers that differ on purpose, this is its own copy for escalate_off_local's
# specific question ("is this ticket's CURRENT model one fworker would charge as a local-
# rung fault"), kept in lockstep by test_router.py::test_local_rung_models_mirrors_fworker
# -- same mirrored-copy-plus-pinning-test pattern as the token-engine daemon's masking
# vocabulary (harness/masking.py / token-engine/token_engine_daemon.py).
LOCAL_RUNG_POOL_MODELS = ("deepseek-v4-flash", "qwen36-local", "nephthys-qwen14b",
                          "hathor-llama8b", "local-worker")
LOCAL_RUNG_MODELS = tuple(dict.fromkeys(
    tuple(m for m in LOCAL_RUNG_POOL_MODELS if m != "deepseek-v4-flash") + ("local-worker",)))


def is_local_rung(model):
    """Same predicate/definition as harness/fworker.py:is_local_rung -- mirrored, not
    imported (see LOCAL_RUNG_MODELS above). escalate_off_local's callers use this to
    decide whether a ticket is even a candidate for local->cloud escalation."""
    return bool(model) and any(model.startswith(m) for m in LOCAL_RUNG_MODELS)


ESCALATED_MARKER = "ESCALATED local->"   # wq#2581: notes substring, the idempotence guard


def escalate_off_local(ticket_id, reason, manifest=None):
    """wq#2581: a local-rung ticket that exhausts its attempts never otherwise reaches a
    cloud model -- fworker.py's own retry ladder keeps re-running args.model unchanged,
    and the dispatcher's SPAWN_LOOP guard just blocks it too. 30d measured (research/
    LOCAL_RUNG_AUDIT_2576.md): 58/103 local tickets blocked, 0/166 local sessions
    all-time classified SUCCESS. Handing an exhausted local ticket UP to the next
    non-local rung in its own archetype chain (rather than sideways to a different local
    rung, and rather than just giving up) is a call select() can already make correctly
    with allow_local=False -- this wraps that call plus the wq edit/note/release side
    effects so both callers (fworker.py's handle_failure, foreman_dispatcher.py's
    handle_spawn_loop) get one answer instead of two ad hoc ones.

    Idempotent per ticket: a ticket whose notes already carry the ESCALATED_MARKER is
    never escalated again, so a ticket that fails again on its NEW (necessarily
    non-local, since allow_local=False can never select one) rung falls through to an
    ordinary block instead of being walked further -- no ping-pong back to local, and no
    silent re-escalation past the caller's own is_local_rung() gate.

    Returns (True, "<model>@<effort>") on a written escalation (ticket is now `open`,
    not blocked, on the new rung), or (False, "<why not>") if there was nothing to
    escalate to (chain has no rung after local, or nothing local to escalate FROM) or
    the ticket was already escalated once. A caller still blocks as normal on False.
    """
    t = wq_show(ticket_id)
    if ESCALATED_MARKER in (t.get("notes") or ""):
        return False, "already escalated once (notes marker present)"
    arch = t.get("archetype")
    if not arch or arch == "None":
        return False, "no archetype set on the ticket, cannot select a next rung"
    manifest = manifest or load_manifest()
    try:
        rung, reasons = select(manifest, arch, fails=1, allow_local=False)
    except ManifestError as e:
        return False, f"bad archetype {arch!r}: {e}"
    if rung is None or rung.model == "DECOMPOSE":
        return False, f"no non-local rung after local for archetype {arch} ({'; '.join(reasons)})"
    p = wq("edit", str(ticket_id), "--model", rung.model, "--effort", rung.effort or "")
    if p.returncode != 0:
        return False, f"wq edit failed: {(p.stdout or p.stderr).strip()}"
    spec = f"{rung.model}@{rung.effort or ''}"
    wq("note", str(ticket_id), f"{ESCALATED_MARKER}{spec} after {reason}")
    p_rel = wq("release", str(ticket_id))
    if p_rel.returncode != 0:
        return False, f"escalated model to {spec} but wq release failed: {(p_rel.stdout or p_rel.stderr).strip()}"
    return True, spec


# RELIABILITY_SPEC.md §E: "systemd-run units: MemoryMax + MemoryHigh + TasksMax +
# CPUQuota; OOMPolicy=stop" on every fworker-wq<ID> unit (wq#648/S3 — none of these were
# wired into dispatch_cmd before this; lumen-run has supported the flags since wq#613,
# just unused here). Sized for the T5810 (44 cores/60G, governor/README.md §floors):
# MemoryMax hard-kills a runaway child well before it can pressure the box; MemoryHigh
# throttles a couple GB ahead of that; CPUQuota leaves the rest of the fleet's own
# concurrent units headroom; TasksMax catches a fork-bomb-shaped tool loop.
# OOMPolicy=stop (not lumen-run's "continue" default): on an OOM kill anywhere in the
# unit's cgroup, cleanly terminate the WHOLE unit (fworker + child) via the normal
# TimeoutStopSec-graced stop path below, rather than leave a supervisor alive over a
# cgroup that just lost a process — §E prefers external verdict-aware supervision (the
# lease reaper, wq#647) picking up a cleanly-stopped unit over relying on fworker's own
# in-process OOM handling to always be the one still standing to do it.
WORKER_MEM_MAX = "8G"
WORKER_MEM_HIGH = "6G"
WORKER_CPU_QUOTA = "400%"
WORKER_TASKS_MAX = "512"

# wq#1261 (#13 item 2): the fire-response archetype's whole fworker session (not just
# its `claude` child — fire_gate.runtime_exceeded() only fires if fworker's own loop is
# still alive to check it) gets wrapped in `timeout` at the dispatch path, so a wedged
# fworker still gets torn down at the cap instead of running forever. Same env var/
# default fire_gate.py's internal watchdog reads, so both layers agree on the cap.
FIRE_ARCHETYPE = "fire-response"
FIRE_MAX_RUNTIME_S = int(os.environ.get("FOREMAN_FIRE_MAX_RUNTIME_S", 2 * 3600))
FIRE_MAX_RUNTIME_KILL_AFTER_S = int(os.environ.get("FOREMAN_FIRE_MAX_RUNTIME_KILL_AFTER_S", "60"))


def repo_for_lane(lane):
    """LANE_REPO lookup, or None if the lane has no canonical repo (caller must pass
    --repo explicitly). Never returns a guess — an unmapped lane is not /home/lumen."""
    return LANE_REPO.get(lane)


# wq#1220: verify-pass tickets (judgment.file_verify_pass) are filed under the static
# verify_pass policy lane (models.yaml verify_pass.lane, normally 'build') so board/
# reporting groups all fleet QA work together regardless of what lane produced the
# diff being verified — but that means LANE_REPO would resolve the ticket's OWN lane
# ('build' -> FOREMAN_ROOT) instead of the SHIPPED ticket's repo (e.g. 'mailbot' ->
# /home/lumen/mailbot), leaving a non-foreman-lane verify worker with no access to the
# code it was dispatched to verify. file_verify_pass writes the shipped ticket's
# resolved repo back into the verify ticket's own detail (same wq#1094 pattern
# repo_sweep uses for the 'auto' lane) instead of overloading lane, so lane stays
# meaningful for dashboards/pacing/reaper messages.
VERIFY_TITLE_PREFIX = "[verify] wq#"
_VERIFY_REPO_RE = re.compile(r"^Verify repo: (\S+)", re.MULTILINE)


def verify_repo_hint(title, detail):
    """wq#1220: read back the shipped ticket's repo a verify-pass ticket carries in its
    own detail (written by judgment.file_verify_pass). Returns None (never a guess)
    unless the title marks a verify-pass ticket AND the parsed path is still a member
    of repo_sweep.managed_repos(), so a hand-edited detail can't point dispatch at an
    arbitrary path."""
    if not title.startswith(VERIFY_TITLE_PREFIX):
        return None
    m = _VERIFY_REPO_RE.search(detail or "")
    if not m:
        return None
    repo = m.group(1)
    return repo if repo in repo_sweep.managed_repos() else None


# ---------------------------------------------------------------------------
# wq#4686 (design/orchestrator_verify_lane.md §5.2/§5.3): route a verify ticket by
# what its DERIVED needs (judgment.file_verify_pass) demand, not by its lane/archetype
# of origin. verify_needs_block_reasons() runs first — any unsatisfiable need means the
# ticket is filed status=blocked and never reaches classify_verify_archetype at all
# (§5.3 is a filing-time gate, not an archetype choice).
# ---------------------------------------------------------------------------

def verify_needs_block_reasons(needs, policy_path=None):
    """[block_reason, ...] for every derived need §5.3 says a verify ticket must never
    be spawned with: an ssh(<node>) policy refuses (THOR_HARDLOCK foremost) or a
    homeroot(<path>) outside VERIFY_HOME_ROOT_READ_ALLOW / hitting the credential
    deny-list. Each reason already names its own rule file:line (fleet_exec_policy.py
    for ssh, capability_lint.py for homeroot) so the caller only has to prefix
    'orchestrator-only: '. Empty list = every derived need is satisfiable (or there are
    none) -- NOT the same as 'route to verify-probe', which is classify_verify_archetype's
    job once this list is empty."""
    reasons = []
    policy = None
    if needs.ssh_nodes:
        try:
            policy = fleet_exec_policy.load_policy(policy_path or capability_lint.FLEET_EXEC_POLICY_PATH)
        except fleet_exec_policy.PolicyError as e:
            reasons.append(f"ssh needs unresolvable: fleet-exec-policy.yaml unreadable: {e}")
    if policy is not None:
        for node in needs.ssh_nodes:
            ok, rule, reason = fleet_exec_policy.check_ssh_node(node, policy)
            if not ok:
                reasons.append(f"ssh({node}) refused by {rule} ({reason}) "
                                f"(fleet_exec_policy.py:{_FLEET_EXEC_SSH_CHECK_LINE})")
    for path in needs.homeroot_paths:
        refusal = capability_lint.homeroot_refusal(path)
        if refusal:
            reasons.append(f"homeroot({path}) refused: {refusal}")
    return reasons


# wq#4686: cached once at import so verify_needs_block_reasons' ssh refusal message can
# cite check_ssh_node's own definition line without re-deriving it per call (the function
# body never moves without this module being re-imported anyway).
def _find_def_line(module_file, name):
    pat = re.compile(rf"^def {re.escape(name)}\(")
    try:
        with open(module_file) as f:
            for i, line in enumerate(f, 1):
                if pat.match(line):
                    return i
    except OSError:
        pass
    return "?"


_FLEET_EXEC_SSH_CHECK_LINE = _find_def_line(fleet_exec_policy.__file__, "check_ssh_node")

# §5.2: needs whose only route is a lane with read-reach beyond plain verify-pass's
# worktree + default add_dirs. ops_verified liveness also demands reach (the probe runs
# install.sh --verify against a live target) but Liveness is a sibling object, never
# merged into Needs (needs.py §4 firewall) — classify_verify_archetype takes it as a
# separate argument rather than reading it off `needs`.
def _verify_needs_demand_reach(needs):
    return bool(needs.ssh_nodes or needs.homeroot_paths
                or "/home/lumen/minerva" in needs.dirs)


def classify_verify_archetype(needs, liveness=None):
    """(archetype, reason) for a verify ticket whose block reasons (if any) are already
    cleared by the caller -- 'verify-probe' (archetype 10) when the derived needs (or an
    ops_verified liveness tag) demand reach beyond plain verify-pass's box, else
    'verify-pass' unchanged (§5.2). This is the ONLY place that picks between the two;
    file_verify_pass calls it, nothing else should duplicate the predicate."""
    if _verify_needs_demand_reach(needs):
        return "verify-probe", "derived needs demand reach (ssh/homeroot/minerva)"
    if liveness:
        return "verify-probe", "ops_verified liveness demands reach to a live target"
    return "verify-pass", "no derived need demands reach beyond verify-pass's envelope"


# wq#433/ISSUE-LOG #56 class (wq#683 questfree, wq#1155 nest, wq#433 LPP-MP): a ticket
# whose lane says one repo and whose TITLE says another dispatches, silently, into the
# wrong tree. The worker then finds none of the paths its ticket names, and — because
# --add-dir grants primary trees READ access only (fworker.py WORKER_ADD_DIRS) — cannot
# write or land in the right one either. Every instance so far burned a full attempt,
# blocked, and got redispatched into the identical misconfiguration (wq#433 twice).
# The lane is not evidence enough on its own when the title names a different repo, so
# refuse rather than guess.
# wq#1331 (§3.2): derived from LANE_REPO at import instead of a hand-kept second table
# that could drift from it (the wq#1325 nest entry did exactly that). Every lane whose
# repo is not FOREMAN_ROOT is a marker (a `[build]`/`[ops]` title tag says nothing a
# foreman lane doesn't already); "lpp-mp" is the one marker with no lane of its own —
# the LPP-MP title prefix wq#433 tickets actually carry.
TITLE_REPO_MARKERS = {
    lane: path for lane, path in LANE_REPO.items() if path != FOREMAN_ROOT
}
TITLE_REPO_MARKERS["lpp-mp"] = LANE_REPO["pokemon"]
_LEADING_TAGS_RE = re.compile(r"^\s*(?:\[([^\]]*)\]\s*)+")
_TAG_RE = re.compile(r"\[([^\]]*)\]")


def title_repo_marker(title):
    """The repo a ticket's title names, or None. Reads the leading `[tag]` run
    (`[lpp-mp] M5d: ...`, `[lpp][stuck] ...`); with no brackets, falls back to the first
    bare word (`LPP-MP M5 (stretch): ...` — wq#433's own title). Only exact
    TITLE_REPO_MARKERS keys match, so an ordinary title like `fix(dispatcher): ...`
    never resolves."""
    title = title or ""
    lead = _LEADING_TAGS_RE.match(title)
    if lead:
        tags = [t.strip().lower() for t in _TAG_RE.findall(lead.group(0))]
    else:
        tags = [title.strip().split()[0].strip("[]():,").lower()] if title.strip() else []
    for tag in tags:
        repo = TITLE_REPO_MARKERS.get(tag)
        if repo:
            return repo
    return None


def repo_mismatch(ticket):
    """wq#433: the reason string for a ticket whose title marker names a different repo
    than its lane resolves to, else None. Verify-pass tickets are exempt — their lane is
    a static policy default and their real repo is pinned in their own detail (wq#1220).
    An explicit --repo still overrides; this only fires where dispatch would otherwise
    derive the repo from the lane alone."""
    title = ticket.get("title", "")
    if title.startswith(VERIFY_TITLE_PREFIX):
        return None
    lane = ticket.get("lane")
    lane_repo = repo_for_lane(lane)
    title_repo = title_repo_marker(title)
    if not title_repo and lane_repo != FOREMAN_ROOT:
        # wq#4623: a REPO_TAGS tag/field overrides only a foreman-default lane; a lane
        # that is itself a repo claim (pokemon, mailbot) disagreeing with it is wq#433.
        title_repo = ticket_repo_tag(ticket)[0]
    if not lane_repo or not title_repo or lane_repo == title_repo:
        return None
    return (f"title names {title_repo} but lane {lane!r} maps to {lane_repo} — "
            f"re-lane the ticket (or pass --repo); a worker in {lane_repo} gets "
            f"READ-only access to {title_repo} and cannot land there")


def needs_repo_hint(ticket):
    """wq#1331 (§3.2): the per-ticket needs:repo(<path>) override, validated against
    repo_sweep.managed_repos() exactly as verify_repo_hint is — a hand-edited tag can't
    point dispatch at an arbitrary path; an unmanaged path falls through to the normal
    resolution order rather than being trusted. FOREMAN_NEEDS_DISABLE=1 (rollback)
    makes needs_mod.parse() return empty, so this returns None."""
    repo = needs_mod.parse(ticket.get("needs"), ticket.get("detail", "")).repo
    return repo if repo and repo in repo_sweep.managed_repos() else None


# wq#1446 (ISSUE-LOG #192, #33): a ticket whose deliverable lives in another managed repo
# (wq#1440 -> lumen-bin) was dispatched into the lane's repo, so the evidence gate re-checked
# every citation against a worktree that could never contain the change. The per-ticket
# needs:repo() tag exists for this, but nobody remembers to write it. The ticket already
# names its output: the `deliverable` column, or an explicit `Deliverable: /abs/path` line.
# Same trust rule as needs_repo_hint: the path must sit inside a managed repo, so a stray
# path cannot point dispatch at an arbitrary tree. FOREMAN_DELIVERABLE_REPO_DISABLE=1 turns
# it off.
_DELIVERABLE_MARK_RE = re.compile(r"(?im)\bdeliverables?\s*[:=]\s*`?(/[\w.@+-]+(?:/[\w.@+-]+)*)`?")


def deliverable_repo_hint(ticket):
    """The managed repo that holds the ticket's named deliverable, or None."""
    if os.environ.get("FOREMAN_DELIVERABLE_REPO_DISABLE") == "1":
        return None
    paths = []
    col = str(ticket.get("deliverable") or "").strip()
    if col:
        paths.append(col)
    paths += _DELIVERABLE_MARK_RE.findall(ticket.get("detail") or "")
    repos = sorted(repo_sweep.managed_repos(), key=len, reverse=True)
    for raw in paths:
        p = os.path.normpath(os.path.expanduser(raw))
        if not os.path.isabs(p):
            continue
        for repo in repos:
            if p == repo or p.startswith(repo + "/"):
                return repo
    return None


# wq#2753 (ISSUE-LOG #728; 4th of the #33/#125/#192 class): wq#2624's detail said
# "Lands via lumen-pr against /home/lumen/bin." — the natural phrasing tickets use for
# their landing repo — but no hint parsed it, so lane 'auto' fell through and the ticket
# dispatched into a foreman worktree whose evidence gate could never verify a lumen-bin
# citation. Same trust rule as deliverable_repo_hint: a path must sit inside a managed
# repo; a bare name ("foreman", "bin") resolves only when it matches the basename of
# exactly one managed repo. Only the instruction-shaped forms match — past-tense
# "landed via lumen-pr ..." is a ticket narrating history, not naming its own target.
# FOREMAN_LANDING_REPO_DISABLE=1 turns it off.
_LANDING_REPO_RE = re.compile(
    r"(?i)\bland(?:s|ing)?\s+via\s+lumen-pr\s+(?:against|on|to|in)\s+"
    r"`?(?P<target>/?[\w.@+-]+(?:/[\w.@+-]+)*)`?")


def landing_repo_hint(ticket):
    """The managed repo a 'land(s) via lumen-pr against/on <target>' sentence in the
    ticket's detail names, or None (never a guess)."""
    if os.environ.get("FOREMAN_LANDING_REPO_DISABLE") == "1":
        return None
    repos = sorted(repo_sweep.managed_repos(), key=len, reverse=True)
    found = []
    for m in _LANDING_REPO_RE.finditer(ticket.get("detail") or ""):
        target = m.group("target").rstrip(".")  # sentence-final period is not path
        if not target:
            continue
        hit = None
        if os.path.isabs(target):
            p = os.path.normpath(target)
            hit = next((r for r in repos if p == r or p.startswith(r + "/")), None)
        else:
            named = [r for r in repos if os.path.basename(r) == target]
            if len(named) == 1:
                hit = named[0]
        if hit and hit not in found:
            found.append(hit)
    # A detail that quotes another ticket's landing sentence (wq#2753's own detail
    # quotes wq#2624's 'against /home/lumen/bin' and then says 'on foreman') names
    # two repos. First-match picked the quoted one; two distinct targets = no hint.
    return found[0] if len(found) == 1 else None


# wq#4623 (ISSUE-LOG #1565): wq#4608, titled `[messenger] ...` with a detail naming
# /home/lumen/messenger, ran in a foreman worktree because 'messenger' is no lane, so
# TITLE_REPO_MARKERS (lanes only) never saw it and lane 'build' won. Repos that have no
# lane of their own are named here; a ticket picks one with a leading `[tag]` or a
# line-start `repo: <tag | /abs/path | owner/name>` field in its detail.
# Most leading tags are topical ([verify], [judgment], [sec]: 88 distinct ones on
# wq#3600+), so an unlisted title tag is only "unknown" when it is the name of a git repo
# under REPO_TAG_ROOT — the shape of wq#4608's [messenger] before this table existed.
REPO_TAGS = {
    "ccc": "/home/lumen/ccc",
    "messenger": "/home/lumen/messenger",
    "foreman": FOREMAN_ROOT,
    "bin": "/home/lumen/bin",
    "minerva": "/home/lumen/minerva",
}
REPO_TAG_ROOT = "/home/lumen"
_REPO_FIELD_RE = re.compile(r"(?im)^[ \t]*repo[ \t]*:[ \t]*`?([^\s`]+)`?")


def _leading_bracket_tags(title):
    lead = _LEADING_TAGS_RE.match(title or "")
    return [t.strip().lower() for t in _TAG_RE.findall(lead.group(0))] if lead else []


def _repo_ref(value):
    """The repo a `repo:` field value names, or None. Accepts a REPO_TAGS / lane-marker
    name, a Gitea `owner/name` (`lumen/lumen-messenger` -> messenger), or an absolute path
    at or inside a REPO_TAGS or managed repo."""
    value = value.rstrip(".,;")
    if os.path.isabs(value):
        p = os.path.normpath(value)
        repos = sorted(set(REPO_TAGS.values()) | repo_sweep.managed_repos(),
                       key=len, reverse=True)
        return next((r for r in repos if p == r or p.startswith(r + "/")), None)
    name = value.rsplit("/", 1)[-1].lower()
    name = name[:-len(".git")] if name.endswith(".git") else name
    name = name[len("lumen-"):] if name.startswith("lumen-") else name
    return REPO_TAGS.get(name) or TITLE_REPO_MARKERS.get(name)


def ticket_repo_tag(ticket):
    """(repo, None) when the ticket names its repo by `repo:` field or REPO_TAGS title tag;
    (None, reason) when it names one dispatch has no row for — the caller skips and pages,
    never falls back to the lane's repo; (None, None) when it names none. Verify-pass
    tickets are exempt (their repo is pinned in their detail, wq#1220)."""
    title = ticket.get("title") or ""
    if title.startswith(VERIFY_TITLE_PREFIX):
        return None, None
    m = _REPO_FIELD_RE.search(ticket.get("detail") or "")
    if m:
        repo = _repo_ref(m.group(1))
        if repo:
            return repo, None
        return None, (f"detail field 'repo: {m.group(1)}' names no repo dispatch knows "
                      f"(router.REPO_TAGS: {', '.join(sorted(REPO_TAGS))}) — add a "
                      f"REPO_TAGS row or fix the field")
    tags = _leading_bracket_tags(title)
    for tag in tags:
        if tag in REPO_TAGS:
            return REPO_TAGS[tag], None
    for tag in tags:
        if (tag and "/" not in tag and tag not in TITLE_REPO_MARKERS
                and os.path.isdir(os.path.join(REPO_TAG_ROOT, tag, ".git"))):
            return None, (f"title tag [{tag}] names git repo {REPO_TAG_ROOT}/{tag}, which "
                          f"has no router.REPO_TAGS row — add one rather than dispatching "
                          f"into the lane's repo")
    return None, None


def repo_for_ticket(ticket_id_or_dict):
    """The one resolver shared by dispatch_cmd and foreman_dispatcher._do_pass (wq#1123):
    a lane/title repo disagreement refuses outright (wq#433 — see repo_mismatch; a
    needs tag never papers over a mis-laned ticket, §5.2), then the ticket's own
    needs:repo tag (wq#1331 — the per-ticket override, ahead of every derived source),
    then the repo holding the ticket's named deliverable (wq#1446), then the repo a
    'lands via lumen-pr against/on ...' sentence in the detail names (wq#2753), then the
    ticket's `repo:` field / REPO_TAGS title tag (wq#4623 — an unknown one returns None
    rather than the lane default), then the verify-pass repo hint (wq#1220 — a verify ticket's own lane is a static
    policy default, not the repo it needs to verify), then the exact repo a repo-sweep
    ticket wrote into its own detail (repo_sweep.repo_hint — per-ticket data, so ahead
    of the lane default since wq#2753; 1eb5c48 mapping 'auto' into LANE_REPO had put
    the foreman default in front of it), then LANE_REPO. Returns None (never a guess)
    if none resolve.

    Accepts either a ticket id (does the wq_show lookup itself) or an already-fetched
    ticket dict, so a caller that already has the ticket doesn't pay for a second show."""
    t = ticket_id_or_dict if isinstance(ticket_id_or_dict, dict) else wq_show(ticket_id_or_dict)
    if repo_mismatch(t):
        return None
    repo = needs_repo_hint(t)
    if repo:
        return repo
    repo = deliverable_repo_hint(t)
    if repo:
        return repo
    repo = landing_repo_hint(t)
    if repo:
        return repo
    repo, unknown = ticket_repo_tag(t)
    if repo or unknown:
        return repo  # wq#4623: an unknown repo tag refuses; never the lane default
    repo = verify_repo_hint(t.get("title", ""), t.get("detail", ""))
    if repo:
        return repo
    # wq#2753: the sweep's own per-ticket repo (wq#1094) must outrank the lane default —
    # 1eb5c48 (wq#2103) mapped 'auto' into LANE_REPO, which silently rerouted every
    # [repo-sweep] auto-lane ticket to foreman regardless of the repo the sweep named.
    repo = repo_sweep.repo_hint(t.get("title", ""), t.get("detail", ""))
    if repo:
        return repo
    lane = t.get("lane")
    if lane == "auto":
        # wq#2870: LANE_REPO['auto'] exists only for repo-sweep-shaped infra tickets,
        # caught by repo_sweep.repo_hint above. A plain auto-lane ticket that matched
        # none of the hints must refuse rather than guess FOREMAN_ROOT.
        return None
    return repo_for_lane(lane)


def dispatch_cmd(ticket_id, repo=None, *, manifest=None, env=None, model_override=None):
    """The fworker command for a ticket whose tuple is already on the ticket (or gets
    assigned now). Mirrors design/T2A §2.

    model_override (wq#2154): the model the worker runs as, in place of the ticket's model
    column. The dispatcher resolves the `local-worker` routing group to a member and spawns
    on that; the column keeps the group name so a released ticket is re-resolved next pass.

    repo: explicit override. Omit it to derive the repo via repo_for_ticket() — LANE_REPO
    (wq#713) first, then a repo-sweep ticket's own repo hint (wq#1094/wq#1123) — raising
    rather than falling back to any default when neither resolves."""
    t = wq_show(ticket_id)
    if not repo:
        mismatch = repo_mismatch(t)
        if mismatch:
            raise SystemExit(f"router: wq#{ticket_id} lane/repo mismatch — {mismatch}")
        repo = repo_for_ticket(t)
        unknown = None if repo else ticket_repo_tag(t)[1]
        if unknown:
            raise SystemExit(f"router: wq#{ticket_id} unknown repo tag — {unknown}")
        if not repo:
            raise SystemExit(
                f"router: wq#{ticket_id} lane {t.get('lane')!r} has no LANE_REPO entry — "
                f"pass --repo explicitly (never guess /home/lumen)")
    model, effort, arch = t.get("model"), t.get("effort"), t.get("archetype")
    manifest = manifest or load_manifest()
    if (model not in (None, "", "None") and manifest.never_for_security(model)
            and is_security_ticket(t, manifest)):
        model = None   # wq#5083: a Fable stamp on a security ticket is re-routed, never run
    if model in (None, "", "None") or prefix_overrides_stored(t):
        d = assign(ticket_id, manifest=manifest)
        if not d.get("rung"):
            raise SystemExit(f"router: cannot route wq#{ticket_id}: {d['reasons']}")
        model, effort, arch = d["rung"]["model"], d["rung"]["effort"], d["archetype"]
    if model_override:
        model = model_override
    g = security_guard(t, model, effort, manifest)   # wq#5083: covers model_override too
    if g:
        model, effort = g[0], g[1]
    # RELIABILITY_SPEC.md §E: TimeoutStopSec>=60 on worker units so a `systemctl stop`/
    # unit-recycle sends SIGTERM and gives fworker's own child-kill escalation
    # (fworker.run_child, SIGTERM then SIGKILL) and the CLI's SessionEnd hooks time to
    # run before systemd would otherwise escalate to SIGKILL itself — margin above the
    # spec floor so fworker's own 30s SIGTERM->SIGKILL wait always fits inside it.
    cmd = ["lumen-run", "start", "--name", f"fworker-wq{ticket_id}",
           "--timeout-stop-sec", "90",
           "--mem", WORKER_MEM_MAX, "--mem-high", WORKER_MEM_HIGH,
           "--cpu", WORKER_CPU_QUOTA, "--tasks-max", WORKER_TASKS_MAX,
           "--oom-policy", "stop",
           "--"]
    inner = ["python3", FWORKER, "run", "--ticket", str(ticket_id), "--repo", repo,
             "--model", model]
    if effort and effort != "None":
        inner += ["--effort", effort]
    if arch and arch != "None":
        inner += ["--archetype", arch]
    if arch == FIRE_ARCHETYPE:
        # wq#1261 (#13 item 2): external wall-clock cap on the WHOLE session, enforced
        # by `timeout` outside fworker's own process — belt-and-braces against
        # fire_gate.runtime_exceeded()'s in-process watchdog never getting scheduled.
        inner = (["timeout", "--signal=TERM", f"--kill-after={FIRE_MAX_RUNTIME_KILL_AFTER_S}",
                  str(FIRE_MAX_RUNTIME_S)] + inner)
    if env:
        # wq#2130: lumen-run forwards only PATH into the unit; per-spawn settings ride in
        # front of the worker command (dispatcher: FOREMAN_FIRST_FRAME_S / FOREMAN_SERVED_CTX)
        inner = ["env"] + [f"{k}={v}" for k, v in sorted(env.items())] + inner
    cmd += inner
    return cmd


DECOMPOSE_PLANNER = os.path.join(FOREMAN_ROOT, "dispatcher", "decompose_planner.py")
DECOMPOSER_UNIT_PREFIX = "decomposer-wq"


def decomposer_cmd(ticket_id):
    """wq#2516 phase 2: the unit command for a DECOMPOSE-routed ticket — the same
    lumen-run shape as dispatch_cmd's worker units, but running the wq#2515 planner
    (dispatcher/decompose_planner.py) instead of an fworker: the parent needs sub-tickets
    filed, not a worker session. Model + effort come from the `decomposer:` routing row
    in research/models.yaml, read by the planner itself — never passed from here."""
    return ["lumen-run", "start", "--name", f"{DECOMPOSER_UNIT_PREFIX}{int(ticket_id)}",
            "--timeout-stop-sec", "90",
            "--mem", WORKER_MEM_MAX, "--mem-high", WORKER_MEM_HIGH,
            "--cpu", WORKER_CPU_QUOTA, "--tasks-max", WORKER_TASKS_MAX,
            "--oom-policy", "stop",
            "--", "python3", DECOMPOSE_PLANNER, "--ticket", str(int(ticket_id))]


# ---- CLI -----------------------------------------------------------------------------

def _pressure_from_args(args):
    if getattr(args, "live", False):
        return governor_pressure
    table = {}
    for spec in getattr(args, "pressure", None) or []:
        m, _, v = spec.partition("=")
        table[resolve_alias(m)] = v
    return (lambda model: table.get(model, "ok")) if table else None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="router.py")
    ap.add_argument("--manifest", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("classify"); s.add_argument("ticket", nargs="?", type=int)
    s.add_argument("--title"); s.add_argument("--detail", default=""); s.add_argument("--lane", default="")

    s = sub.add_parser("route"); s.add_argument("archetype")
    s.add_argument("--fails", type=int, default=0); s.add_argument("--cross-subsystem", action="store_true")
    s.add_argument("--contradiction", action="store_true")
    s.add_argument("--pressure", action="append", metavar="MODEL=ok|tight|deny")
    s.add_argument("--live", action="store_true", help="ask the T1 governor for pressure")
    s.add_argument("--security", action="store_true", help="route as a security ticket (wq#5083)")

    s = sub.add_parser("assign"); s.add_argument("ticket", type=int)
    s.add_argument("--archetype"); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--fails", type=int, default=0); s.add_argument("--cross-subsystem", action="store_true")
    s.add_argument("--pressure", action="append"); s.add_argument("--live", action="store_true")

    s = sub.add_parser("dispatch-cmd"); s.add_argument("ticket", type=int)
    s.add_argument("--repo", default=None, help="override; derived from ticket lane (LANE_REPO) if omitted")
    sub.add_parser("roster")
    sub.add_parser("check")
    args = ap.parse_args(argv)

    manifest = load_manifest(args.manifest, force=bool(args.manifest))
    if args.cmd == "check":
        print(json.dumps({"ok": True, "manifest": manifest.path,
                          "archetypes": sorted(manifest.routing),
                          "dominated_exact": sorted(f"{m}@{e}" for m, e in manifest.dominated_exact),
                          "dominated_scoped": {f"{m}@{e}": sorted(v) for (m, e), v in manifest.dominated_scoped.items()},
                          "dominated_families": manifest.dominated_families,
                          "keep_entitled": manifest.keep_entitled(),
                          "security_chain": [r.spec() for r in manifest.security["chain"]],
                          "security_never": manifest.security["never_model_prefixes"]}, indent=1))
        return 0
    if args.cmd == "roster":
        for r in manifest.roster():
            flags = ("routed " if r["routed"] else "       ") + \
                    ("keep-entitled " if r["keep_entitled"] else "") + \
                    ("family-dominated" if r["family_dominated"] else "")
            print(f"{r['id']:<28} {str(r['status']):<20} {flags}")
        return 0
    if args.cmd == "classify":
        if args.ticket:
            t = wq_show(args.ticket)
            arch, sig = classify_archetype(t.get("title", ""), t.get("detail", ""), t.get("lane", ""), manifest)
        else:
            t = {"title": args.title or "", "detail": args.detail, "lane": args.lane}
            arch, sig = classify_archetype(args.title or "", args.detail, args.lane, manifest)
        print(json.dumps({"archetype": arch, "signals": sig,
                          "security": is_security_ticket(t, manifest)}))
        return 0
    if args.cmd == "route":
        rung, reasons = select(manifest, args.archetype, pressure=_pressure_from_args(args),
                               fails=args.fails, cross_subsystem=args.cross_subsystem,
                               contradiction=args.contradiction, security=args.security)
        print(json.dumps({"archetype": args.archetype, "rung": rung.as_dict() if rung else None,
                          "reasons": reasons}))
        return 0 if rung else 1
    if args.cmd == "assign":
        d = assign(args.ticket, archetype=args.archetype, manifest=manifest,
                   pressure=_pressure_from_args(args), dry_run=args.dry_run,
                   fails=args.fails, cross_subsystem=args.cross_subsystem)
        print(json.dumps(d))
        return 0 if d.get("rung") else 1
    if args.cmd == "dispatch-cmd":
        cmd = dispatch_cmd(args.ticket, args.repo, manifest=manifest)
        print(" ".join(shlex.quote(c) for c in cmd))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
