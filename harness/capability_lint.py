"""wq#1437 (BROKER_WIRING.md §2.1, slice B): derive a ticket's capability needs
from what it cites, deterministically.

`derive(ticket) -> Derived(tags, unresolved, refused)`. Regex + lumen_state node
resolution + the static broker policy; no LLM, no network, no guessing. Three
buckets, never merged:

  tags        derivable AND safe to auto-apply (read-only dir grants, ssh `run`,
              policy-bounded restart, copy into a node's lumen-drop)
  unresolved  an action verb or node the regexes saw but could not pin to one tag
  refused     a derived tag the DIR_DENY list or fleet-exec-policy.yaml forbids

Slice B shipped the derivation in WARN mode (log the buckets, apply nothing). Slice C
(wq#1438, §2.2) makes `apply-dir` the default: the buckets become dispatcher skip
reasons and the `dir` tags become a `wq edit --needs` write. `apply` (slice F) is the
same gate with the action tags applied too.

Rollback ladder: FOREMAN_NEEDS_LINT_MODE=warn restores slice B (derive + log, decide
nothing); =off makes the dispatcher stop calling this module at all;
FOREMAN_NEEDS_DISABLE=1 (needs.py) already disables the consumers downstream.
"""
import fnmatch
import os
import re
import subprocess

LUMEN_STATE_CLI = os.environ.get("FOREMAN_LUMEN_STATE_CLI",
                                 "/home/lumen/minerva/lumen_state.py")
HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
FLEET_EXEC_POLICY_PATH = os.environ.get("FOREMAN_FLEET_EXEC_POLICY",
                                        os.path.join(HARNESS_DIR, "fleet-exec-policy.yaml"))
STATE_TIMEOUT_S = 15

# §2.1: hard-coded in the lint, NOT in policy.yaml — same reasoning as THOR_HARDLOCK
# (fleet_exec_policy.py:26): an edited policy file must not be able to hand a worker
# Frank's tokens. A cited secret is refused and the ticket stays skipped until a human
# rewrites it; a ticket that needs a token is a Frank-hands ticket.
DIR_DENY_PREFIXES = (
    "/home/lumen/.secrets",
    "/home/lumen/secrets",
    "/home/lumen/.ssh",
    "/home/lumen/.claude",
    "/home/lumen/.config",
    "/home/lumen/worktrees",
    "/home/lumen/scratch/worktrees",
)
DIR_DENY_EXACT = ("/home/lumen", "/")
DIR_DENY_PATH_GLOBS = ("/home/lumen/agents/work-queue/queue.db*",)
DIR_DENY_BASENAME_GLOBS = ("*.env", "*.token", "*.key", "*.pem", "id_*")

# fworker.py:820 — the same literal set assert_spawn_paths refuses on, so the lint and
# the spawn assertion can never disagree about what a ticket cites.
_CITED_PATH_RE = re.compile(r"/home/lumen/[\w./~-]+")

_NODE = r"[a-z][a-z0-9_-]*"
_IP_RE = re.compile(r"\b(?:100|10|192\.168)\.\d{1,3}\.\d{1,3}(?:\.\d{1,3})?\b")
_SSH_RE = re.compile(r"\bssh\s+(?:-\S+(?:\s+\S+)?\s+)*(?:(?P<user>[\w.-]+)@)?(?P<host>[\w.-]+)")

def _host_shaped(token):
    """Tuned on the live corpus (wq#879, #325 read `ssh` in prose: "bare ssh itself",
    "ran ssh to bes", "ssh outcome"). An `ssh <target>` whose target is not a roster
    subject is only reported as an unknown node when the target LOOKS like a host —
    it carries a dot, a hyphen, a digit or a user@ — because a false `unresolved` is a
    ticket slice C would refuse to spawn, while a missed one still fails closed at the
    broker (fleet-exec:119 unknown_node)."""
    return any(c in token for c in ".-@") or any(c.isdigit() for c in token)
_PROBE_ON_RE = re.compile(
    r"\b(?:check|checks|probe|probes|verify|verifies|inspect|read|reads|run|runs|ls|"
    r"grep|tail|journalctl|df|status|log|logs|test|tests)\b[^.\n]{0,80}?\bon\s+"
    rf"(?P<node>{_NODE})\b")
_RESTART_TAG_RE = re.compile(rf"\brestart\((?P<unit>[\w.@-]+)@(?P<node>{_NODE})\)")
_SYSTEMCTL_RE = re.compile(
    r"\bsystemctl\s+(?:--[\w-]+(?:=\S+)?\s+)*(?:restart|reload|start)\s+"
    r"(?P<unit>[\w.@-]+)(?:\s+on\s+(?P<node>" + _NODE + r"))?")
_RESTART_ON_RE = re.compile(
    rf"\brestart\s+(?P<unit>[\w.@-]+?)(?:\.service)?\s+on\s+(?P<node>{_NODE})\b")
_COPY_RE = re.compile(
    rf"\b(?:scp|rsync)\b[^\n]{{0,120}}?\b(?P<node>{_NODE}):(?P<dir>/[\w./-]+)")
# v2 verbs (§2.4 "not in v1, still"): seen, never tagged — the ticket is honest about
# needing something the broker cannot do yet.
_V2_VERB_RE = re.compile(
    r"\b(?P<verb>sudo|adb|fastboot|reboot|poweroff|shutdown|power-cycle|"
    r"docker\s+restart)\b")

DEFAULT_RESTART_NODE = "lumen"  # §2.1: "node omitted = lumen"


class Derived:
    """Three buckets. `tags` is (name, arg) pairs in needs.py's vocabulary."""

    def __init__(self, tags=(), unresolved=(), refused=()):
        self.tags, self.unresolved, self.refused = [], [], []
        for t in tags:
            if t not in self.tags:
                self.tags.append(t)
        for u in unresolved:
            if u not in self.unresolved:
                self.unresolved.append(u)
        for r in refused:
            if r not in self.refused:
                self.refused.append(r)

    @property
    def tag_strings(self):
        return [f"{name}({arg})" for name, arg in self.tags]

    def needs_field(self):
        """The value slice C will hand `wq edit --needs` (needs.py:98 validates it)."""
        return ", ".join(self.tag_strings)

    def as_event(self):
        return {"tags": self.tag_strings,
                "unresolved": list(self.unresolved),
                "refused": list(self.refused)}

    def __bool__(self):
        return bool(self.tags or self.unresolved or self.refused)

    def __repr__(self):
        return f"Derived(tags={self.tag_strings}, unresolved={self.unresolved}, refused={self.refused})"


# ---- mode flag (rollback) ------------------------------------------------------

MODE_OFF, MODE_WARN, MODE_APPLY_DIR, MODE_APPLY = "off", "warn", "apply-dir", "apply"
MODES = (MODE_OFF, MODE_WARN, MODE_APPLY_DIR, MODE_APPLY)
DEFAULT_MODE = MODE_APPLY_DIR  # wq#1438 slice C; `warn` is the documented rollback
ENFORCING_MODES = (MODE_APPLY_DIR, MODE_APPLY)
# Which derived tags the dispatcher may write back per mode. `apply-dir` is slice C's
# whole scope: `dir` is read-only and deny-listed (§2.1), so auto-applying it can only
# ever widen a worker's READ context. ssh/restart/copy stay derive-and-refuse until
# slice F has test (viii)'s three clean adversarial cycles behind it.
APPLIED_TAG_NAMES = {MODE_APPLY_DIR: ("dir",),
                     MODE_APPLY: ("dir", "ssh", "restart", "copy")}


def mode():
    """`off` | `warn` (slice B: derive + log only) | `apply-dir` (slice C default:
    gate + write `dir` tags) | `apply` (slice F: gate + write action tags too).

    An unrecognised value degrades to `warn`, never to an enforcing mode: a typo in a
    systemd drop-in must not be able to make the whole queue undispatchable."""
    m = (os.environ.get("FOREMAN_NEEDS_LINT_MODE") or DEFAULT_MODE).strip().lower()
    return m if m in MODES else MODE_WARN


def enabled():
    return mode() != MODE_OFF


def enforcing():
    """True when the buckets are dispatcher decisions, not just log lines (§2.2)."""
    return mode() in ENFORCING_MODES


def appliable_tags(derived):
    """The subset of `derived.tags` this mode lets the dispatcher write back."""
    names = APPLIED_TAG_NAMES.get(mode(), ())
    if not derived or not names:
        return []
    return [t for t in derived.tags if t[0] in names]


# ---- node roster (lumen_state) -------------------------------------------------

class Roster:
    """Fleet subjects with a `tailscale_ip`/`lan_ip` fact, plus the ip -> subject map.
    An unreachable state store yields an EMPTY roster, which makes every node mention
    `unresolved` — fail closed, never guess a node (fleet-exec:60 does the same)."""

    def __init__(self, names=(), ips=None):
        self.names = set(names)
        self.ips = dict(ips or {})

    def node_for_ip(self, ip):
        return self.ips.get(ip)

    def known(self, node):
        return node in self.names


_FACT_RE = re.compile(r"^\s*(?P<subject>[\w-]+)\.(?P<pred>[\w-]+)\s*=\s*(?P<value>\S+)")
_roster_cache = None


def _state_facts(predicate):
    try:
        proc = subprocess.run(["python3", LUMEN_STATE_CLI, "current", "", predicate],
                              capture_output=True, text=True, timeout=STATE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    out = []
    for line in proc.stdout.splitlines():
        m = _FACT_RE.match(line)
        if m and m.group("pred") == predicate:
            out.append((m.group("subject"), m.group("value")))
    return out


def load_roster(refresh=False):
    """Roster from lumen_state, cached for the life of the process (one dispatcher
    pass calls derive() once per candidate; the fleet roster does not move mid-pass)."""
    global _roster_cache
    if _roster_cache is not None and not refresh:
        return _roster_cache
    names, ips = set(), {}
    for pred in ("tailscale_ip", "lan_ip"):
        for subject, value in _state_facts(pred):
            names.add(subject)
            # an ip claimed by two subjects resolves to neither (§2.1 "exactly one")
            ips[value] = None if value in ips and ips[value] != subject else subject
    _roster_cache = Roster(names, {k: v for k, v in ips.items() if v})
    return _roster_cache


# ---- static broker policy (refusals that are not the dir deny-list) -------------

_policy_cache = {}


def load_policy(path=None):
    """fleet-exec-policy.yaml as a dict; unreadable/invalid -> {} (the lint then makes
    no policy-based refusal and copy targets stay `unresolved` — fail closed, since a
    tag is only ever emitted for a dir the policy explicitly lists)."""
    path = path or FLEET_EXEC_POLICY_PATH
    if path in _policy_cache:
        return _policy_cache[path]
    data = {}
    try:
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f.read()) or {}
    except Exception:
        data = {}
    _policy_cache[path] = data
    return data


def restart_refusal(node, unit, policy):
    """(refused_reason | None) for a restart tag under fleet-exec-policy.yaml's
    `never` rows. Mirrors fleet_exec_policy.check_restart's data source; the lint
    never emits a tag the broker would deny at call time."""
    never = (policy or {}).get("never", {}) or {}
    if node in (never.get("nodes") or []):
        return f"policy:never.nodes ({node})"
    for glob in ((never.get("restart") or {}).get(node) or []):
        if fnmatch.fnmatch(unit, glob):
            return f"policy:never.restart[{node}] ({glob})"
    return None


# ---- dir tags ------------------------------------------------------------------

def _norm(path):
    return os.path.normpath(path)


def dir_denied(path):
    """(reason | None): the §2.1 hard-coded deny-list, applied to the path AS CITED
    (before any normalisation) and again to the directory it normalises to."""
    p = _norm(path)
    if p in DIR_DENY_EXACT:
        return f"deny:path ({p})"
    for prefix in DIR_DENY_PREFIXES:
        if p == prefix or p.startswith(prefix + os.sep):
            return f"deny:prefix ({prefix})"
    for glob in DIR_DENY_PATH_GLOBS:
        if fnmatch.fnmatch(p, glob):
            return f"deny:path_glob ({glob})"
    base = os.path.basename(p)
    for glob in DIR_DENY_BASENAME_GLOBS:
        if fnmatch.fnmatch(base, glob):
            return f"deny:basename ({glob})"
    return None


def _under(path, root):
    path, root = _norm(path), _norm(root)
    return path == root or path.startswith(root + os.sep)


# One seam for every filesystem question the lint asks, so a test can pin a corpus
# ticket's expected bucket to a fixed set of paths instead of to this host's disk.
def _isdir(path):
    return os.path.isdir(path)


def _exists(path):
    return os.path.exists(path)


def normalise_dir(path):
    """The deepest EXISTING directory for a cited path: the path itself if it is a
    directory, its parent if it is a file. A path that does not exist is NOT walked
    up to some ancestor — that would be a guess (§2.1) — and returns None."""
    p = _norm(path)
    if _isdir(p):
        return p
    if _exists(p):
        return os.path.dirname(p)
    return None


def cited_paths(text):
    """fworker.cited_paths (:828) over title+detail rather than detail alone."""
    out = []
    for m in _CITED_PATH_RE.finditer(text or ""):
        p = m.group(0).rstrip(".,;:")
        if p != "/home/lumen" and p not in out:
            out.append(p)
    return out


def _derive_dirs(text, repo, add_dirs, tags, unresolved, refused):
    roots = [r for r in [repo, *add_dirs] if r]
    for p in cited_paths(text):
        if any(_under(p, r) for r in roots):
            continue  # already reachable: repo ∪ add_dirs, no tag needed
        why = dir_denied(p)
        if why:
            refused.append(f"dir({p}) {why}")
            continue
        d = normalise_dir(p)
        if d is None:
            unresolved.append(f"cited path {p} does not exist (not guessed)")
            continue
        why = dir_denied(d)
        if why:
            refused.append(f"dir({d}) {why}")
            continue
        if any(_under(d, r) for r in roots):
            continue
        tags.append(("dir", d))


# ---- action tags ---------------------------------------------------------------

def _derive_ssh(text, roster, tags, unresolved):
    for m in _SSH_RE.finditer(text):
        host = m.group("host")
        if _IP_RE.fullmatch(host):
            node = roster.node_for_ip(host)
            if not node:
                unresolved.append(f"ssh {host}: no single lumen_state subject for that ip")
                continue
        elif roster.known(host):
            node = host
        elif _host_shaped(host) or m.group("user"):
            unresolved.append(f"ssh {host}: node {host!r} is not a lumen_state subject")
            continue
        else:
            continue  # prose, not a node mention
        tags.append(("ssh", node))
    for m in _PROBE_ON_RE.finditer(text):
        node = m.group("node")
        if roster.known(node):
            tags.append(("ssh", node))
    for m in _IP_RE.finditer(text):
        node = roster.node_for_ip(m.group(0))
        if node:
            tags.append(("ssh", node))


def _add_restart(node, unit, roster, policy, tags, unresolved, refused, context):
    if not roster.known(node):
        unresolved.append(f"{context}: node {node!r} is not a lumen_state subject")
        return
    why = restart_refusal(node, unit, policy)
    if why:
        refused.append(f"restart({unit}@{node}) {why}")
        return
    tags.append(("restart", f"{unit}@{node}"))


def _derive_restart(text, roster, policy, tags, unresolved, refused):
    for m in _RESTART_TAG_RE.finditer(text):
        _add_restart(m.group("node"), m.group("unit"), roster, policy,
                     tags, unresolved, refused, f"restart({m.group('unit')}@{m.group('node')})")
    for m in _SYSTEMCTL_RE.finditer(text):
        node = m.group("node") or DEFAULT_RESTART_NODE
        _add_restart(node, m.group("unit"), roster, policy,
                     tags, unresolved, refused, f"systemctl restart {m.group('unit')}")
    for m in _RESTART_ON_RE.finditer(text):
        _add_restart(m.group("node"), m.group("unit"), roster, policy,
                     tags, unresolved, refused,
                     f"restart {m.group('unit')} on {m.group('node')}")


def _derive_copy(text, roster, policy, tags, unresolved):
    copy_dirs = (policy or {}).get("copy_dirs", {}) or {}
    for m in _COPY_RE.finditer(text):
        node, d = m.group("node"), _norm(m.group("dir"))
        if not roster.known(node):
            unresolved.append(f"copy to {node}:{d}: node is not a lumen_state subject")
            continue
        if d not in [_norm(x) for x in (copy_dirs.get(node) or [])]:
            unresolved.append(f"copy to {node}:{d}: not a copy_dirs entry for {node}")
            continue
        tags.append(("copy", f"{node}:{d}"))


def _derive_v2(text, unresolved):
    for m in _V2_VERB_RE.finditer(text):
        verb = re.sub(r"\s+", " ", m.group("verb"))
        unresolved.append(f"{verb!r} is a v2 verb: no fleet-exec grant exists for it")


# ---- entry point ---------------------------------------------------------------

def base_add_dirs(archetype=None):
    """The `--add-dir` list a worker of this archetype gets before any needs:dir tag
    (fworker.add_dirs_for's `base`, :1074). Read from agents.yaml through the same
    spec loader so the lint's idea of "already reachable" cannot drift from the
    spawner's. An unreadable manifest yields [] — the lint then over-reports dir tags
    rather than silently under-reporting a path the worker cannot reach."""
    try:
        import agents_spec
        spec = agents_spec.load()
        if archetype and archetype in spec:
            return list(spec.get(archetype).add_dirs)
        return list(spec.default_add_dirs)
    except Exception:
        return []


def ticket_text(ticket):
    return f"{ticket.get('title') or ''}\n{ticket.get('detail') or ''}"


def derive(ticket, repo=None, add_dirs=(), roster=None, policy=None):
    """Derived(tags, unresolved, refused) for one ticket dict (`title`, `detail`).

    `repo`/`add_dirs` are the reach the worker already has — a path inside them needs
    no tag. `roster`/`policy` are injectable so a test never touches the live fleet.
    """
    text = ticket_text(ticket)
    roster = roster if roster is not None else load_roster()
    policy = policy if policy is not None else load_policy()
    tags, unresolved, refused = [], [], []
    _derive_dirs(text, repo, add_dirs, tags, unresolved, refused)
    _derive_ssh(text, roster, tags, unresolved)
    _derive_restart(text, roster, policy, tags, unresolved, refused)
    _derive_copy(text, roster, policy, tags, unresolved)
    _derive_v2(text, unresolved)
    return Derived(tags, unresolved, refused)


# ---- corpus scan (tuning, §2.1 "tune against the real corpus") -----------------

QUEUE_DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")


def scan_queue(db_path=None, statuses=("open", "blocked"), repo_for=None):
    """[(ticket, Derived)] over the live queue — the corpus this lint is tuned on and
    the same read slice C's dispatcher stage will make one ticket at a time.
    `repo_for(ticket) -> path` supplies each ticket's repo (router.repo_for_ticket in
    the dispatcher; None here means "no reach", the worst case)."""
    import sqlite3
    db_path = db_path or QUEUE_DB
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    qmarks = ",".join("?" for _ in statuses)
    rows = con.execute(
        f"select id, lane, status, title, detail, needs from tasks where status in ({qmarks})"
        " order by id", tuple(statuses)).fetchall()
    con.close()
    roster, policy = load_roster(), load_policy()
    out = []
    for row in rows:
        t = dict(row)
        repo = repo_for(t) if repo_for else None
        out.append((t, derive(t, repo=repo, roster=roster, policy=policy)))
    return out


def _main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(description="capability_lint corpus scan (read-only)")
    ap.add_argument("--db", default=QUEUE_DB)
    ap.add_argument("--status", action="append", default=None)
    ap.add_argument("--ticket", type=int, default=None, help="scan one ticket id")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    statuses = tuple(a.status or ("open", "blocked"))
    scanned = scan_queue(a.db, statuses)
    if a.ticket:
        scanned = [(t, d) for t, d in scanned if t["id"] == a.ticket]
    n_tags = n_unres = n_ref = 0
    for t, d in scanned:
        if d.tags:
            n_tags += 1
        if d.unresolved:
            n_unres += 1
        if d.refused:
            n_ref += 1
        if not d:
            continue
        if a.json:
            print(json.dumps({"id": t["id"], "status": t["status"], **d.as_event()}))
        else:
            print(f"wq#{t['id']} [{t['status']}] tags={d.tag_strings} "
                  f"unresolved={len(d.unresolved)} refused={d.refused}")
    print(f"-- {len(scanned)} scanned: {n_tags} with tags, {n_unres} unresolved, "
          f"{n_ref} refused")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
