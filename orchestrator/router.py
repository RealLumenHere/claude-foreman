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
  3. Effort-first downgrade (§10 CONFIRMED): under soft budget pressure the same model
     steps DOWN one effort (never into dominated territory, never below the model's own
     ladder) before the chain moves to a cheaper model. A hard governor deny for a model
     skips straight to the next model in the chain — a lower effort cannot un-deny a pool.
  4. Escalation rungs are annotated in the manifest ("(escalate-on-fail)",
     "(2-fails-or-cross-subsystem)", "(on-contradiction)") and only become eligible when
     the caller's facts satisfy the annotation.
  5. Refusal awareness (CLAUDE_CLI_INTERNALS §C): the CLI itself re-routes a refused turn
     (bio -> opus-5, cyber -> opus-4-8, opus-5-origin bio = hard refuse). The router never
     SELECTS opus-4-8 (dominated by opus-5) but reports it as keep_entitled and can tell a
     supervisor where a refusal will land (`refusal_landing`).

CLI (all read-only except `assign`, which writes the tuple into wq via `wq edit`):
    router.py classify  <ticket-id> | --title T [--detail D] [--lane L]
    router.py route     <archetype> [--attempt N] [--fails N] [--cross-subsystem]
                        [--pressure MODEL=ok|tight|deny ...] [--live]
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
import repo_sweep  # noqa: E402  # wq#1123: shared repo_for_ticket() resolver

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
# wq#830: 'auto' is deliberately NOT listed. It is wq's CLI default lane (`wq add` with no
# --lane) and so spans arbitrary repos — wq#688 targets questfree, wq#306 vault, wq#545
# wq-tooling. Mapping it to FOREMAN_ROOT made `dispatch-cmd <auto-ticket>` emit a silently
# wrong --repo. Do not re-add it without the per-entry Frank confirmation every other
# non-obvious entry below carries; the honest fix for an auto ticket is an explicit --repo.
# wq#1094: the one exception is repo-sweep tickets, which carry their own exact target
# repo in the ticket's own detail (harness/repo_sweep.py wrote it there) — the dispatcher
# reads that back (repo_sweep.repo_hint) instead of using this table. Still not a guess:
# it's per-ticket data, validated against repo_sweep.managed_repos() before use.
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
    "nest": "/home/lumen/projects/nest",  # wq#1155/ISSUE-LOG #56 class: [nest]-titled tickets
                                           # were dispatching lane=build -> FOREMAN_ROOT with
                                           # zero access to the actual nest repo
    "pstack": "/home/lumen/pstack",
    "mission-control": "/home/lumen/mission-control",
    "fleet-spend": FOREMAN_ROOT,
    "ops": FOREMAN_ROOT,
    "quest-repair-research": FOREMAN_ROOT,
    "fleet-fires": FOREMAN_ROOT,          # fires are fleet/infra work, run from foreman
    "mailbot": "/home/lumen/mailbot",     # lumen@ email agent (wq#1163 v2, wq#1164-1174); Frank 2026-09-01 "go ahead with the build"
}
WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
GOVERNOR_CLI = os.environ.get("FOREMAN_GOVERNOR_CLI",
                               os.path.join(FOREMAN_ROOT, "governor", "foreman_usaged.py"))
STATE_JSON = os.environ.get("FOREMAN_STATE_JSON", os.path.join(FOREMAN_ROOT, "state.json"))
FWORKER = os.path.join(FOREMAN_ROOT, "harness", "fworker.py")

EFFORT_LADDER = ["low", "medium", "high", "xhigh", "max"]

# short name in routing/dominated tables -> full CLI id. Anything already starting with
# "claude-" or listed as a local model id passes through unchanged.
ALIASES = {
    "fable-5": "claude-fable-5", "fable": "claude-fable-5",
    "opus-5": "claude-opus-5", "opus": "claude-opus-5",
    "sonnet-5": "claude-sonnet-5", "sonnet": "claude-sonnet-5",
    "haiku-4-5": "claude-haiku-4-5", "haiku": "claude-haiku-4-5",
    "opus-4-8": "claude-opus-4-8", "opus-4-7": "claude-opus-4-7", "opus-4-6": "claude-opus-4-6",
    "sonnet-4-6": "claude-sonnet-4-6",
}

# FOREMAN_RESEARCH §15 numbering — dominated rows say "(archetypes 1-2)".
ARCHETYPE_NUMBERS = {
    1: "mechanical-edit", 2: "probe-build", 3: "standard-engineering",
    4: "rca-investigation", 5: "novel-design", 6: "long-horizon-campaign",
    7: "research-synthesis", 8: "verify-pass",
    9: "fire-response",  # wq#717 (AUTO-5): fleet-fires manual-approval workflow
}
ARCHETYPES = list(ARCHETYPE_NUMBERS.values())
# routing rows that are not ticket archetypes: the foreman's own judgment sessions.
EXTRA_ROUTING_ROWS = {"orchestrator"}

# Soft-pressure margin: remaining% above the governor floor below which the router steps
# effort down first (doctrine 3). Above it: full tuple. Below the floor: governor denies.
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
        return list(self.routing[archetype])

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
        if problems:
            raise ManifestError("; ".join(problems))
        return True


_MANIFEST = None


def load_manifest(path=None, force=False):
    global _MANIFEST
    if _MANIFEST is not None and not force and path is None:
        return _MANIFEST
    p = path or MANIFEST_PATH
    if yaml is None:
        raise ManifestError("PyYAML not installed")
    with open(p) as f:
        data = yaml.safe_load(f)
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


def effort_down(manifest, model, effort, archetype=None):
    """One effort step down on the same model, skipping dominated tuples. None if there
    is no legal lower step (then the chain must move to the next model)."""
    if effort is None:
        return None
    ok = [e for e in EFFORT_LADDER if e in manifest.efforts_for(model)]
    if effort not in ok:
        return None
    i = ok.index(effort)
    for e in reversed(ok[:i]):
        if not manifest.is_dominated(model, e, archetype):
            return e
    return None


def candidates(manifest, archetype, fails=0, cross_subsystem=False, contradiction=False):
    """The eligible chain for this ticket's facts, in preference order, dominated rungs
    removed (defensively; check() already refuses manifests that contain them)."""
    out = []
    for r in manifest.chain(archetype):
        if not _condition_met(r.condition, fails, cross_subsystem, contradiction):
            continue
        if r.model != "DECOMPOSE" and manifest.is_dominated(r.model, r.effort, archetype):
            continue
        out.append(r)
    return out


def select(manifest, archetype, *, pressure=None, fails=0, cross_subsystem=False,
           contradiction=False, allow_local=True):
    """Pick the (model, effort) for one dispatch.

    pressure(model) -> 'ok' | 'tight' | 'deny'   (default: everything 'ok')
      tight = soft budget pressure -> effort-first downgrade on that model
      deny  = governor refuses that model -> next model in the chain
    Escalation facts (fails, cross_subsystem, contradiction) unlock annotated rungs; when
    any escalation rung is unlocked it is preferred over the primary (that is what
    escalation means), still subject to pressure.

    Returns (Rung, [reasons]) or (None, [reasons]) if nothing is dispatchable.
    """
    pressure = pressure or (lambda m: "ok")
    reasons = []
    cands = candidates(manifest, archetype, fails, cross_subsystem, contradiction)
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
    # recorded fail now walks `base` forward by one rung — same doctrine as effort-first
    # downgrade under pressure, triggered by attempt count instead. Never walks past the
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
        p = pressure(r.model) if r.pool == "cloud" else "ok"
        if p == "deny":
            reasons.append(f"skip {r}: governor denies {r.model}")
            continue
        model, effort = r.model, r.effort
        if p == "tight" and effort:
            e2 = effort_down(manifest, model, effort, archetype)
            if e2:
                reasons.append(f"{model}: soft pressure, effort-first downgrade {effort}->{e2}")
                effort = e2
            else:
                reasons.append(f"{model}@{effort}: soft pressure but no legal lower effort")
        effort = manifest.clamp_effort(model, effort)
        why = manifest.is_dominated(model, effort, archetype)
        if why:            # cannot happen given effort_down, but never emit a dominated tuple
            reasons.append(f"skip {model}@{effort}: {why}")
            continue
        if (model, effort) in seen:
            continue
        seen.add((model, effort))
        chosen = Rung(model, effort, r.pool, r.condition)
        reasons.append(f"selected {chosen.spec()}")
        return chosen, reasons
    reasons.append("every rung denied or dominated")
    return None, reasons


# ---- archetype classification (FOREMAN_RESEARCH §15 rubric) --------------------------

_RUBRIC = [
    # (archetype, [regex signals]) — evaluated in this order; first hit wins unless a later
    # stronger signal is listed as an override below.
    ("verify-pass", [r"\bverify[- ]pass\b", r"\bfresh[- ]eyes\b", r"\badversarial\b",
                     r"\bre-?check\b", r"\beval gate\b", r"\baudit of (the )?fix\b"]),
    ("long-horizon-campaign", [r"\bcampaign\b", r"\bmulti-session\b", r"\bcheckpoint",
                               r"\buplift\b", r"\bpivot\b", r"\bchain\b"]),
    ("novel-design", [r"\bdesign( doc)?\b", r"\bnew (service|subsystem|daemon)\b",
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
}

# Archetypes whose routing is expensive/consequential enough that a single WEAK signal
# must not win them alone: the opus-primary pair (wq#778) plus long-horizon-campaign,
# whose primary rung is DECOMPOSE (wq#992).
_CORROBORATION_ARCHETYPES = _OPUS_PRIMARY_ARCHETYPES | {"long-horizon-campaign"}


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
    text = f"{title or ''}\n{detail or ''}"
    by_arch = {}
    for arch, pats in _RUBRIC:
        matched = [p for p in pats if re.search(p, text, re.IGNORECASE)]
        if matched:
            by_arch[arch] = matched
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
    # lane bias breaks ties among qualifying hit archs
    for b in _LANE_BIAS.get(lane or "", []):
        if b in qualifying:
            return b, by_arch[b]
    incumbent = qualifying[0]
    if incumbent in _CHEAP_ARCHETYPES or len(qualifying) == 1:
        return incumbent, by_arch[incumbent]
    # wq#779 F3: cost-aware resolution, not list-order-first-wins — restricted to the two
    # structurally-outranked archetypes above. A cheap archetype whose evidence is at
    # least as strong as the incumbent's should not lose just because it sits lower in
    # _RUBRIC; among multiple eligible cheap archetypes the cheaper primary rung wins.
    incumbent_n = len(by_arch[incumbent])
    cheap = [a for a in qualifying if a in _CHEAP_ARCHETYPES and len(by_arch[a]) >= incumbent_n]
    if not cheap:
        return incumbent, by_arch[incumbent]
    manifest = manifest or load_manifest()
    best = min(cheap, key=lambda a: _primary_rung_cost(manifest, a))
    return best, by_arch[best]


# ---- governor pressure adapter ------------------------------------------------------

def governor_pressure(model, *, state_path=STATE_JSON, governor_cli=GOVERNOR_CLI,
                      soft_margin=SOFT_MARGIN_PCT):
    """'deny' if `foreman_usaged.py can-dispatch MODEL` refuses; else 'tight' when any pool
    the model draws from is within soft_margin points of its floor (or flagged
    warning/critical by the governor); else 'ok'. Fail-closed: unreadable state = 'deny'."""
    try:
        p = subprocess.run([sys.executable, governor_cli, "can-dispatch", model],
                           capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            return "deny"
    except (OSError, subprocess.TimeoutExpired):
        return "deny"
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
    checks = [("session", floors.get("session_min_remaining_pct", 20)),
              ("weekly_all", floors.get("weekly_all_min_remaining_pct", 15))]
    if "fable" in model:
        checks.append(("fable_weekly_scoped", floors.get("fable_weekly_min_remaining_pct", 50)))
    for pool, floor in checks:
        b = limits.get(pool) or {}
        if b.get("percent") is None:
            return "deny"
        remaining = 100 - b["percent"]
        if remaining < floor:
            return "deny"
        if remaining - floor < soft_margin or b.get("severity") in ("warning", "critical"):
            return "tight"
    return "ok"


# ---- wq glue -------------------------------------------------------------------------

def wq(*args):
    return subprocess.run([WQ_BIN, *args], capture_output=True, text=True)


def wq_show(ticket_id):
    out = {}
    for line in wq("show", str(ticket_id)).stdout.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    if not out or out.get("id") is None:
        raise SystemExit(f"router: no such ticket {ticket_id}")
    return out


def assign(ticket_id, *, archetype=None, manifest=None, pressure=None, dry_run=False,
           fails=0, cross_subsystem=False):
    """Classify (unless told), select, and write (model, effort, archetype) into the
    ticket. Returns the decision dict."""
    manifest = manifest or load_manifest()
    t = wq_show(ticket_id)
    arch = archetype or (t.get("archetype") if t.get("archetype") not in (None, "", "None") else None)
    signals = []
    if not arch:
        arch, signals = classify_archetype(t.get("title", ""), t.get("detail", ""), t.get("lane", ""), manifest)
    subs = [s for s in (t.get("subsystems") or "").split(",") if s]
    cross = cross_subsystem or len(subs) > 1
    rung, reasons = select(manifest, arch, pressure=pressure, fails=fails, cross_subsystem=cross)
    decision = {"ticket": int(ticket_id), "archetype": arch, "signals": signals,
                "rung": rung.as_dict() if rung else None, "reasons": reasons,
                "written": False}
    if rung is None or rung.model == "DECOMPOSE" or dry_run:
        return decision
    p = wq("edit", str(ticket_id), "--model", rung.model, "--effort", rung.effort or "",
           "--archetype", arch)
    decision["written"] = p.returncode == 0 and "edited" in p.stdout
    decision["wq"] = (p.stdout + p.stderr).strip()
    return decision


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


def repo_for_ticket(ticket_id_or_dict):
    """The one resolver shared by dispatch_cmd and foreman_dispatcher._do_pass (wq#1123):
    the verify-pass repo hint first (wq#1220 — a verify ticket's own lane is a static
    policy default, not the repo it needs to verify), then LANE_REPO, then — for lanes
    LANE_REPO doesn't cover (e.g. 'auto') — the exact repo a repo-sweep ticket wrote
    into its own detail (repo_sweep.repo_hint). Returns None (never a guess) if none
    resolve.

    Accepts either a ticket id (does the wq_show lookup itself) or an already-fetched
    ticket dict, so a caller that already has the ticket doesn't pay for a second show."""
    t = ticket_id_or_dict if isinstance(ticket_id_or_dict, dict) else wq_show(ticket_id_or_dict)
    repo = verify_repo_hint(t.get("title", ""), t.get("detail", ""))
    if repo:
        return repo
    repo = repo_for_lane(t.get("lane"))
    if repo:
        return repo
    return repo_sweep.repo_hint(t.get("title", ""), t.get("detail", ""))


def dispatch_cmd(ticket_id, repo=None, *, manifest=None):
    """The fworker command for a ticket whose tuple is already on the ticket (or gets
    assigned now). Mirrors design/T2A §2.

    repo: explicit override. Omit it to derive the repo via repo_for_ticket() — LANE_REPO
    (wq#713) first, then a repo-sweep ticket's own repo hint (wq#1094/wq#1123) — raising
    rather than falling back to any default when neither resolves."""
    t = wq_show(ticket_id)
    if not repo:
        repo = repo_for_ticket(t)
        if not repo:
            raise SystemExit(
                f"router: wq#{ticket_id} lane {t.get('lane')!r} has no LANE_REPO entry — "
                f"pass --repo explicitly (never guess /home/lumen)")
    model, effort, arch = t.get("model"), t.get("effort"), t.get("archetype")
    if model in (None, "", "None"):
        d = assign(ticket_id, manifest=manifest)
        if not d.get("rung"):
            raise SystemExit(f"router: cannot route wq#{ticket_id}: {d['reasons']}")
        model, effort, arch = d["rung"]["model"], d["rung"]["effort"], d["archetype"]
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
    cmd += inner
    return cmd


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
                          "keep_entitled": manifest.keep_entitled()}, indent=1))
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
            arch, sig = classify_archetype(args.title or "", args.detail, args.lane, manifest)
        print(json.dumps({"archetype": arch, "signals": sig}))
        return 0
    if args.cmd == "route":
        rung, reasons = select(manifest, args.archetype, pressure=_pressure_from_args(args),
                               fails=args.fails, cross_subsystem=args.cross_subsystem,
                               contradiction=args.contradiction)
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
