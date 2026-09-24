#!/usr/bin/env python3
"""capability.py — wq#1436, BROKER_WIRING.md §1 + §2.5 (slice A).

One capability check, shared. BROKER_WIRING.md §1 names the class of defect: the
only reachability/grant check the fleet had (`fworker.assert_spawn_paths`,
`fworker.resolve_exec_grant`) ran INSIDE the spawned child — after a slot was taken,
after a worktree was cut — and reported through a failure record the dispatcher only
reads on the next pass. The fix is not a second copy of that logic next to the
dispatcher's own stages; it is this module, which both callers use:

    fworker      belt-and-braces at spawn (a hand-spawned `lumen-run fworker` must
                 still refuse) — `assert_spawn_paths` and `resolve_exec_grant` are
                 now adapters over `preflight()` / `policy_grants()`.
    dispatcher   pre-skip stage 3.7 (slice C, wq#1438): ask `preflight()` "would this
                 spawn refuse?" before a slot is burned, over the needs the ticket
                 carries UNIONED with the ones capability_lint derives
                 (`effective_needs`), then write the safe half back
                 (`apply_tags`) so the next pass has them on the ticket.

A second copy is a lint failure — tests/test_capability.py asserts fworker imports
this module and holds no private copy of the path regex or the policy loop.

Three refusal reasons, all deterministic (regex + policy dict + state store, never a
model judgement), each also a dispatcher skip reason:

    cited_path_unreachable       a needs:dir that is not a directory, or a
                                 /home/lumen/ literal in the detail that lands
                                 outside worktree ∪ repo ∪ add_dirs (wq#1152)
    node_unknown                 an ssh/copy/restart tag names a node that is not a
                                 lumen_state subject with a tailscale_ip fact AND
                                 does not have a Host entry in the ssh config —
                                 fleet-exec's known_nodes() checks only the first, so
                                 such a node passes the broker's gate and then dies
                                 at transport time
    capability_denied_by_policy  a copy/restart tag fleet-exec-policy.yaml forbids
                                 outright (never.nodes, THOR_HARDLOCK, unit globs),
                                 or an unreadable policy file — a refusal, never a
                                 silent downgrade to the allowed half

Rollback: FOREMAN_NEEDS_DISABLE=1 downgrades every refusal to log-only —
`preflight()` returns ok with `downgraded=True` and the summary it would have
refused with, so the caller logs instead of raising.
"""
import glob
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import needs as needs_mod  # noqa: E402

sys.path.insert(0, "/home/lumen/bin")
import fleet_exec_policy  # noqa: E402

REASON_CITED_PATH_UNREACHABLE = "cited_path_unreachable"
REASON_NODE_UNKNOWN = "node_unknown"
REASON_CAPABILITY_DENIED_BY_POLICY = "capability_denied_by_policy"
# wq#1438 §2.2: not a preflight() verdict — the lint's `unresolved` bucket, which only
# the dispatcher can act on (there is no tag to check, which is the point). Lives here
# so the four capability skip reasons are one list in one module.
REASON_NEEDS_UNRESOLVED = "needs_unresolved"

# SpawnRefused code per reason: the dispatcher skips on the lowercase reason, the
# worker raises the uppercase code, and both name the same verdict.
SPAWN_CODE = {
    REASON_CITED_PATH_UNREACHABLE: "CITED_PATH_UNREACHABLE",
    REASON_NODE_UNKNOWN: "NODE_UNKNOWN",
    REASON_CAPABILITY_DENIED_BY_POLICY: "CAPABILITY_DENIED_BY_POLICY",
}

DEFAULT_POLICY_PATH = os.environ.get(
    "FOREMAN_FLEET_EXEC_POLICY", "/home/lumen/foreman/harness/fleet-exec-policy.yaml")
WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
WQ_TIMEOUT_S = 30
LUMEN_STATE = os.environ.get("FOREMAN_LUMEN_STATE", "/home/lumen/minerva/lumen_state.py")
# fleet-exec's known_nodes() (fleet-exec:60) reads the first of these only; the ssh
# alias half is what makes the node actually addressable. Override for tests.
SSH_CONFIG_PATHS = [p for p in os.environ.get(
    "FOREMAN_SSH_CONFIG",
    os.path.expanduser("~/.ssh/config") + os.pathsep
    + os.path.expanduser("~/.ssh/fleet_config")).split(os.pathsep) if p]

_STATE_TAILSCALE_RE = re.compile(r"^\s*(?P<subject>[\w-]+)\.tailscale_ip\s*=")
_SSH_HOST_RE = re.compile(r"^\s*Host\s+(?P<aliases>.+?)\s*$", re.IGNORECASE)
_SSH_INCLUDE_RE = re.compile(r"^\s*Include\s+(?P<patterns>.+?)\s*$", re.IGNORECASE)
_INCLUDE_MAX_DEPTH = 3


class Verdict:
    """The answer both callers act on. `ok` is the only thing a caller must branch
    on; `reason` is the dispatcher's skip reason and `code` the worker's SpawnRefused
    code. `unreachable_paths` and `refused` are the structured fields §2.5 writes into
    failure.json — the auto-triage reads those, never the summary string."""

    def __init__(self, ok, reason=None, summary="", unreachable_paths=None,
                 refused=None, nodes_unknown=None, downgraded=False):
        self.ok = ok
        self.reason = reason
        self.summary = summary
        self.unreachable_paths = list(unreachable_paths or [])
        self.refused = list(refused or [])
        self.nodes_unknown = list(nodes_unknown or [])
        self.downgraded = downgraded

    @property
    def code(self):
        return SPAWN_CODE.get(self.reason)

    def __repr__(self):
        return (f"Verdict(ok={self.ok}, reason={self.reason!r}, "
                f"summary={self.summary!r})")


def _under(path, root):
    path, root = os.path.normpath(path), os.path.normpath(root)
    return path == root or path.startswith(root + os.sep)


def cited_paths(detail):
    """capability_lint.cited_paths — one parser, so the lint and the spawn wall agree on
    what is a citation and what is a mention (wq#2528)."""
    import capability_lint  # local, as effective_add_dirs: no import cycle
    return capability_lint.cited_paths(detail)


# ---------------------------------------------------------------------------
# path reachability (moved body of fworker.assert_spawn_paths, wq#1331 §3.2)
# ---------------------------------------------------------------------------

def path_problems(detail, roots, needs_dirs, advisory_paths=()):
    """(problems, unreachable_paths). Every needs:dir must exist as a directory and
    every /home/lumen/ literal the detail cites must be inside roots — a cited path
    outside the reachable set is the exact wq#1152 signature.

    wq#2184: a cited literal in `advisory_paths` (the lint's advisory bucket) is a prose
    mention, not a request, so being unreachable does not hold the ticket; it dispatches
    with no grant for it. A needs:dir the ticket asked for is never skipped this way."""
    problems = []
    unreachable = []
    for d in needs_dirs or []:
        if not os.path.isdir(d):
            problems.append(f"needs:dir({d}) is not an existing directory")
            unreachable.append(d)
    roots = [r for r in roots if r]
    for p in cited_paths(detail):
        if p in advisory_paths:
            continue
        if not any(_under(p, r) for r in roots):
            problems.append(f"cited path {p} outside worktree ∪ add_dirs")
            if p not in unreachable:
                unreachable.append(p)
    return problems, unreachable


# ---------------------------------------------------------------------------
# node reachability (§2.2: state-store subject AND an ssh alias)
# ---------------------------------------------------------------------------

def state_nodes(state_output=None):
    """Fleet subjects that declare a tailscale_ip, same source and same fail-closed
    behaviour as fleet-exec's known_nodes(): an unreachable minerva yields an empty
    roster, which refuses every node rather than skipping the check."""
    if state_output is None:
        try:
            p = subprocess.run(
                ["python3", LUMEN_STATE, "current", "", "tailscale_ip"],
                capture_output=True, text=True, timeout=15)
            state_output = p.stdout if p.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            state_output = ""
    return {m.group("subject") for m in
            (_STATE_TAILSCALE_RE.match(line) for line in state_output.splitlines()) if m}


def ssh_config_hosts(paths=None, _depth=0, _seen=None):
    """Host aliases from the ssh config(s), following `Include` one fleet-config
    layer deep — an alias behind an Include is just as addressable as an inline one,
    and treating it as unknown would refuse work that ssh can do. Patterns (`Host *`,
    `Host !x`) are dropped: `*` matches everything, so honouring it would make every
    node look addressable and defeat the check."""
    hosts = set()
    seen = _seen if _seen is not None else set()
    for path in (paths if paths is not None else SSH_CONFIG_PATHS):
        path = os.path.abspath(os.path.expanduser(path))
        if path in seen:
            continue
        seen.add(path)
        try:
            with open(path) as f:
                text = f.read()
        except OSError:
            continue
        for line in text.splitlines():
            m = _SSH_HOST_RE.match(line)
            if m:
                hosts.update(a for a in m.group("aliases").split()
                             if not any(c in a for c in "*?!"))
                continue
            m = _SSH_INCLUDE_RE.match(line)
            if m and _depth < _INCLUDE_MAX_DEPTH:
                for pat in m.group("patterns").split():
                    pat = os.path.expanduser(pat)
                    if not os.path.isabs(pat):
                        pat = os.path.join(os.path.dirname(path), pat)
                    hosts |= ssh_config_hosts(sorted(glob.glob(pat)),
                                              _depth=_depth + 1, _seen=seen)
    return hosts


def node_reachable(node, state_output=None, ssh_hosts=None):
    """A node the fleet can actually address: a lumen_state subject with a
    tailscale_ip fact AND an ssh Host entry. A node in the state store with no ssh
    alias passes fleet-exec's known_nodes() today and then fails at transport time —
    that is the failure this half of the preflight moves earlier."""
    if not node:
        return False
    hosts = ssh_config_hosts() if ssh_hosts is None else ssh_hosts
    return node in state_nodes(state_output) and node in hosts


def needs_nodes(needs):
    """Every node an exec-shaped tag names, in tag order, deduped."""
    out = []
    for node in (list(needs.ssh_nodes)
                 + [n for n, _ in needs.copy_targets]
                 + [n for n, _ in needs.restart_targets]):
        if node not in out:
            out.append(node)
    return out


# ---------------------------------------------------------------------------
# policy half of resolve_exec_grant (wq#1332 §2.2 point 2) — no grant file here
# ---------------------------------------------------------------------------

def policy_grants(needs, policy_path=None):
    """(grants, refused): the grant entries fleet-exec-policy.yaml would allow, and
    one refusal string per tag it forbids. Pure policy — the caller decides whether
    to write a grant.json (fworker) or merely ask whether it would (dispatcher).
    ssh(node) is always grantable as read-only `run`: the argv allowlist is enforced
    by the broker per-invocation, not knowable at spawn time."""
    grants = []
    refused = []
    wants_exec = bool(needs.ssh_nodes or needs.copy_targets or needs.restart_targets)
    if not wants_exec:
        return grants, refused
    try:
        policy = fleet_exec_policy.load_policy(policy_path or DEFAULT_POLICY_PATH)
    except fleet_exec_policy.PolicyError as e:
        return [], [f"fleet-exec-policy.yaml unreadable: {e}"]
    for node in needs.ssh_nodes:
        grants.append({"kind": "ssh", "node": node, "verbs": ["run"]})
    for node, remote_dir in needs.copy_targets:
        ok, rule, reason = fleet_exec_policy.check_copy(node, remote_dir, policy)
        if not ok:
            refused.append(f"needs:copy({node}:{remote_dir}) denied by {rule}: {reason}")
            continue
        grants.append({"kind": "copy", "node": node, "dir": remote_dir})
    for node, unit in needs.restart_targets:
        ok, rule, reason = fleet_exec_policy.check_restart(node, unit, policy)
        if not ok:
            refused.append(f"needs:restart({unit}@{node}) denied by {rule}: {reason}")
            continue
        grants.append({"kind": "restart", "node": node, "unit": unit})
    return grants, refused


# ---------------------------------------------------------------------------
# the shared gate
# ---------------------------------------------------------------------------

def preflight(ticket, repo, add_dirs, needs, worktree=None, policy_path=None,
              state_output=None, ssh_hosts=None, advisory_paths=()):
    """Can a worker for this ticket reach what the ticket cites and do what it asks?

    `ticket` is a wq row (only `detail` is read). `worktree` is the worker's own tree
    when there is one — at dispatch time there is not, and repo ∪ add_dirs is the
    reachable set. Checks run cheapest-first and the first failure wins, so a ticket
    that cites an unreachable path AND names an unknown node reports the path: fixing
    the reason a verdict names is always a step forward.

    `advisory_paths` (wq#2184) is the lint's advisory bucket for this ticket
    (`Derived.advisory_paths` / `capability_lint.advisory_paths_for`) — both walls pass
    the same classification. Ignored under FOREMAN_CAPABILITY_ADVISORY=off, so the
    lever restores the hold here even for a caller holding a stale set. node_unknown
    and policy refusals are untouched."""
    if needs is None:
        needs = needs_mod.Needs([])
    detail = (ticket or {}).get("detail", "") if hasattr(ticket, "get") else (ticket or "")
    roots = [worktree, repo] + list(add_dirs or [])
    if advisory_paths:
        import capability_lint  # local, as effective_add_dirs: no import cycle
        if not capability_lint.advisory_enabled():
            advisory_paths = ()

    problems, unreachable = path_problems(detail, roots, needs.dirs, advisory_paths)
    if problems:
        return _verdict(REASON_CITED_PATH_UNREACHABLE, "; ".join(problems),
                        unreachable_paths=unreachable)

    unknown = [n for n in needs_nodes(needs)
               if not node_reachable(n, state_output=state_output, ssh_hosts=ssh_hosts)]
    if unknown:
        return _verdict(REASON_NODE_UNKNOWN,
                        "; ".join(f"node {n} is not a fleet subject with a tailscale_ip "
                                  f"and an ssh Host entry" for n in unknown),
                        nodes_unknown=unknown)

    _, refused = policy_grants(needs, policy_path=policy_path)
    if refused:
        return _verdict(REASON_CAPABILITY_DENIED_BY_POLICY, "; ".join(refused),
                        refused=refused)

    return Verdict(True, summary="ok")


def _verdict(reason, summary, **fields):
    """A refusal, downgraded to an ok-with-summary under the rollback flag so
    FOREMAN_NEEDS_DISABLE=1 means log-only in both callers, not just in fworker."""
    code = SPAWN_CODE[reason]
    if needs_mod.disabled():
        return Verdict(True, summary=f"{code} ({summary}) — FOREMAN_NEEDS_DISABLE=1, "
                                     f"log-only", downgraded=True, **fields)
    return Verdict(False, reason=reason, summary=f"{code} ({summary})", **fields)


# ---------------------------------------------------------------------------
# effective needs + auto-tag write-back (wq#1438 slice C, §2.2)
# ---------------------------------------------------------------------------

def effective_needs(ticket, derived=None):
    """§2.2 line 1: `ticket.needs ∪ lint.tags`.

    The ticket's own tags come from both authored sources (`needs` column and a
    `Needs:` detail line, needs.parse); `derived` is capability_lint's `tags` bucket,
    which has not been written back yet on this pass. Preflight must see the union or
    a ticket would be refused for a path the very same pass is about to grant it.

    FOREMAN_NEEDS_DISABLE=1 is honoured by needs.parse() returning empty; the derived
    half is dropped here for the same reason, so the rollback flag really does mean
    "needs tags decide nothing"."""
    parsed = needs_mod.parse((ticket or {}).get("needs"), (ticket or {}).get("detail"))
    if derived is None or needs_mod.disabled():
        return parsed
    return needs_mod.Needs(list(parsed.tags) + list(derived.tags))


def effective_add_dirs(archetype, needs):
    """The `--add-dir` set a worker for this ticket WOULD get — computed at dispatch
    time, before any spawn. Mirrors fworker.add_dirs_for (:1049) exactly: the
    archetype's base list from agents.yaml, unioned with the ticket's needs:dir tags,
    order preserved. Two functions, one answer; the base half is read through
    capability_lint.base_add_dirs so both read the same manifest loader."""
    import capability_lint  # local: capability_lint imports nothing from this module
    out = list(capability_lint.base_add_dirs(archetype))
    for d in (needs.dirs if needs is not None else []):
        if d not in out:
            out.append(d)
    return out


def covered_by(paths, add_dirs):
    """True when EVERY path is inside one of add_dirs (§2.2 hold release). Empty
    `paths` is False, not vacuously True: a failure record with no structured
    unreachable_paths tells us nothing, and "nothing to cover" must never be read as
    "the defect is fixed" (that is the pre-slice-A failure.json shape, §2.5)."""
    roots = [r for r in (add_dirs or []) if r]
    if not paths or not roots:
        return False
    return all(any(_under(p, r) for r in roots) for p in paths)


def _wq(*args):
    p = subprocess.run([WQ_BIN, *args], capture_output=True, text=True,
                       timeout=WQ_TIMEOUT_S)
    if p.returncode != 0:
        raise RuntimeError(f"wq {args[0]} failed ({p.returncode}): "
                           f"{(p.stderr or p.stdout).strip()[:200]}")
    return p.stdout


def apply_tags(tid, tags, current_needs=None, dry_run=False):
    """§2.1 auto-tag: union `tags` into the ticket's `needs` column via
    `wq edit --needs` and record what changed with `wq note`. Returns the needs field
    written, or None when there was nothing new to write.

    Union, never overwrite — a human-authored tag the lint cannot derive must survive
    an auto-tag pass — and dedupe, so a ticket re-linted every pass gets exactly one
    note line per genuinely new tag (§4 row vi). Written by the DISPATCHER: a worker
    has no `wq edit` in its allowlist (§3), so this is the single writer.

    The field is re-validated with needs.validate() before the write: `wq edit`
    validates too, but a lint that can emit an unstorable tag should fail here, where
    the caller logs it, rather than as an opaque non-zero exit from the CLI."""
    existing = needs_mod.parse_tags(current_needs)
    merged, added = list(existing), []
    for t in tags or []:
        if t not in merged:
            merged.append(t)
            added.append(t)
    if not added:
        return None
    field = ", ".join(f"{name}({arg})" for name, arg in merged)
    ok, err = needs_mod.validate(field)
    if not ok:
        raise ValueError(f"capability_lint derived an unstorable needs field: {err}")
    if dry_run:
        return field
    _wq("edit", str(tid), "--needs", field)
    _wq("note", str(tid), "capability_lint: "
        + " ".join(f"+{name}({arg})" for name, arg in added))
    return field



# ---------------------------------------------------------------------------
# Liveness Gate (research/LIVENESS_GATE.md §3/§4) — sibling of the needs: checks
# above, FIREWALLED from them: preflight() and policy_grants() do not call this,
# and this never returns a Verdict, a grant, or a SpawnRefused code.
# ---------------------------------------------------------------------------

LIVENESS_NO_TAG = "NO_TAG"


def liveness_verdict(ticket, bindings=None):
    """Re-probe a frank-gated ticket's premise against live state.

    Returns (verdict, results) where verdict is liveness.SATISFIED (the gate is
    STALE — already done), liveness.STILL_BLOCKED (Frank genuinely needed),
    liveness.UNKNOWN (fail-safe: leave the ticket alone) or LIVENESS_NO_TAG (no
    `Liveness:` line and no ledger binding — nothing to check). results is the list
    of (probe, arg_text, verdict, detail) so a report can show its work.

    `bindings` is the ledger's {ticket_id: "probe(args), ..."} staging map for
    tickets that predate the tag (§7); it is consulted ONLY when the ticket has no
    `Liveness:` line of its own. Read-only: this function never writes anywhere."""
    import liveness  # local: keeps the exec-grant import surface unchanged
    tid = str((ticket or {}).get("id", ""))
    lv = needs_mod.parse_liveness((ticket or {}).get("detail"))
    if not lv and bindings and tid in {str(k) for k in bindings}:
        bound = next(v for k, v in bindings.items() if str(k) == tid)
        lv = needs_mod.parse_liveness(liveness_field=bound)
    if not lv:
        return LIVENESS_NO_TAG, []
    results = []
    for probe, arg_text in lv:
        verdict, detail = liveness.run(probe, arg_text)
        results.append((probe, arg_text, verdict, detail))
    return liveness.aggregate([(v, d) for _, _, v, d in results]), results
