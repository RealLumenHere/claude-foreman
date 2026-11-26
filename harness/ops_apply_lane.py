"""ops_apply_lane — the ops-apply lane's spawn gates and grant (wq#4688).

Design: design/orchestrator_verify_lane.md §3 option B + §6 (gate table), and
docs/agent-authority-research/DESIGN.md §3.7 (this lane is the non-root half; the
ops-apply root daemon, wq#4764, is the only root path and is a separate mechanism).

What a ticket in this lane may do, end to end:
  * it is NEVER dispatched by a timer: dispatcher/config.json lists the lane with
    enabled:false (the scan allowlist skips it), and a ticket only gets here because the
    orchestrator ran `wq edit <id> --lane ops-apply` and then spawned it by hand;
  * fworker.do_attempt() calls evaluate() before any worktree or token exists; every
    failure is an uncharged SpawnRefused, one code per gate, in this fixed order:

      0. manifest   OPS_APPLY_MANIFEST_INVALID  one ```ops-apply block in the detail
      1. report     GATE_MISSING               `gate:` names an existing report that
                                               names the patch sha / docker argv and unit
      2. judge      JUDGE_NOT_PASS             newest judge_verdicts row for this ticket
                                               bound to (report, manifest) is `allow`
      3. precheck   PRECHECK_MISSING           lumen_episode.py precheck verdict in notes
      4. backup     BACKUP_MISSING             cited backup exists and is a backup of the
                                               CURRENT live state (ops_apply_staged)
      5. target     CAPABILITY_DENIED_BY_POLICY node != thor; unit in allow.restart and
                                               not never.*; tree / docker argv allowlisted

  * the grant it then mints pins every operand (patch sha, backup, unit, exact docker
    argv + inverse, post-check), so the worker can only choose WHETHER to call
    `fleet-exec apply-staged` / `docker-exec-allowlisted`, never what they do;
  * a post-check mismatch inside the broker restores the backup (or runs the pinned
    inverse argv) and `wq block`s the ticket — the worker is not trusted to roll back.

The judge binding (gate 2) is a sha256 over the report bytes and the canonical
manifest, so editing either after the judge said `allow` voids the PASS. `binding`
below prints the exact value and judge command for the orchestrator.

The backup format and patch rules live in /home/lumen/bin/ops_apply_staged.py and the
target checks in /home/lumen/bin/fleet_exec_policy.py (check_apply_staged,
check_docker_exec) so the spawner and the broker share one implementation. Until the
lumen-bin half of wq#4688 is installed those names are absent and gates 4/5 refuse —
the same getattr fail-closed shape capability.py uses for check_peer (wq#3200).

There is no FOREMAN_NEEDS_DISABLE-style off switch: a mutation lane's gates are not
rollback-able to "log only". Rollback is removing the lane (see the bundle README).
"""
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import shlex
import sqlite3

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
LANE = "ops-apply"
SETTINGS_FILE = os.path.join(HARNESS_DIR, "permission-profiles", "ops-apply.json")
DEFAULT_POLICY_PATH = os.path.join(HARNESS_DIR, "fleet-exec-policy.yaml")
JUDGE_DB = os.environ.get("FOREMAN_OPS_APPLY_JUDGE_DB", "/home/lumen/minerva/minerva.db")
BIN_DIR = os.environ.get("FOREMAN_OPS_APPLY_BIN_DIR", "/home/lumen/bin")

GATES = ("manifest", "report", "judge", "precheck", "backup", "target")
CODES = {
    "manifest": "OPS_APPLY_MANIFEST_INVALID",
    "report": "GATE_MISSING",
    "judge": "JUDGE_NOT_PASS",
    "precheck": "PRECHECK_MISSING",
    "backup": "BACKUP_MISSING",
    "target": "CAPABILITY_DENIED_BY_POLICY",
}
BINDING_PREFIX = "ops-apply-binding:"

# lumen_episode.py precheck prints exactly one of these three verdict shapes
# (minerva/lumen_episode.py precheck()); any of them in the notes is the record.
PRECHECK_RE = re.compile(
    r"(\d+ related past failure/lesson\(s\) found"
    r"|\d+ related prior episode\(s\) found"
    r"|No prior episodes on this\. Proceed)")

_FENCE_RE = re.compile(r"^```ops-apply[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
_NODE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_UNIT_RE = re.compile(r"^[A-Za-z0-9@_.:-]{1,128}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_KEYS = ("node", "tree", "patch", "patch_sha256", "backup", "backup_of", "restart",
         "docker_container", "docker_argv", "docker_inverse_argv",
         "postcheck_http", "postcheck_argv", "postcheck_expect")
_ARGV_KEYS = ("docker_argv", "docker_inverse_argv", "postcheck_argv")
_PATH_KEYS = ("tree", "patch", "backup", "backup_of")
THOR = "thor"  # mirrors fleet_exec_policy.THOR_HARDLOCK; asserted here before any import


class GateRefusal(Exception):
    def __init__(self, gate, summary):
        super().__init__(f"{CODES[gate]}: {summary}")
        self.gate = gate
        self.code = CODES[gate]
        self.summary = f"ops-apply gate {GATES.index(gate)} ({gate}): {summary}"


def is_ops_apply(ticket):
    return (ticket or {}).get("lane") == LANE


_MODULES = None


def _load(name):
    path = os.path.join(BIN_DIR, f"{name}.py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location(f"_ops_apply_lane_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bin_modules():
    """(fleet_exec_policy or None, ops_apply_staged or None), loaded BY PATH from
    BIN_DIR — not via sys.modules, where fworker already holds the live
    fleet_exec_policy — so use_bin_dir() can point the gates at a patched copy (tests,
    and the install bundle's pre-install check)."""
    global _MODULES
    if _MODULES is None:
        _MODULES = (_load("fleet_exec_policy"), _load("ops_apply_staged"))
    return _MODULES


def use_bin_dir(path):
    global BIN_DIR, _MODULES
    BIN_DIR, _MODULES = path, None


# ---------------------------------------------------------------------------
# gate 0: manifest
# ---------------------------------------------------------------------------

def parse_manifest(detail):
    """The one ```ops-apply block in the ticket detail -> normalized dict. Strict: an
    unknown or repeated key, a missing required key, or two mutation kinds is a
    refusal, never a best-effort guess at what the orchestrator meant."""
    blocks = _FENCE_RE.findall(detail or "")
    if len(blocks) != 1:
        raise GateRefusal("manifest", f"expected exactly one ```ops-apply block in the "
                                      f"detail, found {len(blocks)}")
    m = {}
    for raw in blocks[0].splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or key not in _KEYS:
            raise GateRefusal("manifest", f"unknown manifest line {line[:80]!r}")
        if key in m:
            raise GateRefusal("manifest", f"{key} given twice")
        if not value:
            raise GateRefusal("manifest", f"{key} is empty")
        if key in _ARGV_KEYS:
            try:
                value = shlex.split(value)
            except ValueError as e:
                raise GateRefusal("manifest", f"{key}: {e}") from None
        m[key] = value
    if not _NODE_RE.match(m.get("node", "")):
        raise GateRefusal("manifest", "node is required (lowercase fleet node name)")
    for k in _PATH_KEYS:
        if k in m and (not m[k].startswith("/") or os.path.normpath(m[k]) != m[k]):
            raise GateRefusal("manifest", f"{k} must be a normalized absolute path")
    is_apply = any(k in m for k in ("tree", "patch", "patch_sha256"))
    is_docker = any(k in m for k in ("docker_container", "docker_argv", "docker_inverse_argv"))
    if is_apply == is_docker:
        raise GateRefusal("manifest", "exactly one mutation per ticket: either "
                                      "tree/patch (apply-staged) or docker_* (docker-exec)")
    need = (("tree", "patch", "patch_sha256", "backup") if is_apply else
            ("docker_container", "docker_argv", "docker_inverse_argv", "backup", "backup_of"))
    missing = [k for k in need if k not in m]
    if missing:
        raise GateRefusal("manifest", f"missing {', '.join(missing)}")
    if is_apply and not _SHA256_RE.match(m["patch_sha256"]):
        raise GateRefusal("manifest", "patch_sha256 must be 64 lowercase hex")
    if "restart" in m and (is_docker or not _UNIT_RE.match(m["restart"])):
        raise GateRefusal("manifest", "restart is a single unit name, apply-staged only")
    if ("postcheck_http" in m) == ("postcheck_argv" in m) or "postcheck_expect" not in m:
        raise GateRefusal("manifest", "exactly one of postcheck_http / postcheck_argv, "
                                      "plus postcheck_expect, is required")
    if "postcheck_http" in m and not m["postcheck_http"].startswith(("http://", "https://")):
        raise GateRefusal("manifest", "postcheck_http must be http(s)")
    m["kind"] = "apply" if is_apply else "docker_exec"
    return m


def strip_manifest(detail):
    """The detail with the ```ops-apply block replaced by a marker. The manifest's paths
    (tree, patch, backup) are BROKER operands pinned into grant.json, not files the
    worker reads, so fworker's cited-path reachability wall must not see them — a
    /home/lumen/backups citation would otherwise refuse every ops-apply spawn."""
    return _FENCE_RE.sub("[ops-apply manifest: broker operands, pinned in grant.json]\n",
                         detail or "")


def canonical(manifest):
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"))


def postcheck_spec(m):
    if "postcheck_http" in m:
        return {"http": m["postcheck_http"], "expect": m["postcheck_expect"]}
    return {"argv": list(m["postcheck_argv"]), "expect": m["postcheck_expect"]}


# ---------------------------------------------------------------------------
# gate 1: report at the ticket's gate: path
# ---------------------------------------------------------------------------

def report_path(ticket):
    """The first enforced (absolute path, `:exists` or bare) clause of the ticket's
    `gate` field — the same clause shape wq's own gate_met() enforces at claim time."""
    for clause in [c.strip() for c in (ticket.get("gate") or "").split(",") if c.strip()]:
        path, _, cond = clause.partition(":")
        if path.startswith("/") and cond in ("", "exists"):
            return path
    return None


def check_report(ticket, m):
    path = report_path(ticket)
    if not path:
        raise GateRefusal("report", "ticket has no `gate:` report path "
                                    "(wq edit <id> --gate /abs/report.md)")
    if not os.path.isfile(path) or os.path.islink(path):
        raise GateRefusal("report", f"verify report {path} does not exist")
    with open(path, "rb") as f:
        data = f.read()
    text = data.decode("utf-8", "replace")
    if m["kind"] == "apply":
        named = [m["patch_sha256"]] + ([m["restart"]] if "restart" in m else [])
    else:
        named = [m["docker_container"], " ".join(m["docker_argv"])]
    absent = [n for n in named if n not in text]
    if absent:
        raise GateRefusal("report", f"report {path} does not name {absent} — the judged "
                                    f"report must name the exact diff/argv and unit")
    return path, data


def binding(report_bytes, m):
    h = hashlib.sha256()
    h.update(b"ops-apply-binding-v1\0")
    h.update(hashlib.sha256(report_bytes).hexdigest().encode())
    h.update(b"\0")
    h.update(canonical(m).encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# gate 2: judge PASS bound to (report, manifest)
# ---------------------------------------------------------------------------

def check_judge(ticket_id, bind, db_path=None):
    """PASS = the NEWEST judge_verdicts row for this ticket whose context carries this
    exact binding has verdict `allow` and is not fail-closed. allow-with-conditions is
    not a PASS (conditions this lane cannot stage), and a later deny overrides an
    earlier allow. Read-only sqlite; any read error is a refusal."""
    db_path = db_path or JUDGE_DB
    actors = (f"wq{ticket_id}", f"wq#{ticket_id}", str(ticket_id))
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute(
                "SELECT ts, verdict, fail_closed FROM judge_verdicts "
                "WHERE actor IN (?,?,?) AND instr(context, ?) > 0 "
                "ORDER BY ts DESC LIMIT 1",
                (*actors, BINDING_PREFIX + bind)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise GateRefusal("judge", f"judge_verdicts unreadable ({e})") from None
    if row is None:
        raise GateRefusal("judge", f"no judge verdict for wq{ticket_id} bound to "
                                   f"{BINDING_PREFIX}{bind[:16]}… (run `ops_apply_lane.py "
                                   f"binding --ticket {ticket_id}`)")
    ts, verdict, fail_closed = row
    if verdict != "allow" or fail_closed:
        raise GateRefusal("judge", f"newest bound verdict ({ts}) is "
                                   f"{verdict or 'FAIL_CLOSED'}{' fail-closed' if fail_closed else ''}, not allow")
    return ts


# ---------------------------------------------------------------------------
# gate 3: precheck recorded in notes
# ---------------------------------------------------------------------------

def check_precheck(ticket):
    if not PRECHECK_RE.search(ticket.get("notes") or ""):
        raise GateRefusal("precheck", "no `lumen_episode.py precheck` verdict in the ticket "
                                      "notes (lumen-change-safety gate 2)")


# ---------------------------------------------------------------------------
# gate 4: backup cited and a backup of the CURRENT state
# ---------------------------------------------------------------------------

def check_backup(m):
    _, staged = _bin_modules()
    if m["kind"] == "docker_exec":
        backup, of = m["backup"], m["backup_of"]
        if not os.path.exists(backup):
            raise GateRefusal("backup", f"backup {backup} does not exist")
        if not os.path.exists(of):
            raise GateRefusal("backup", f"backup_of {of} does not exist")
        if os.path.getmtime(backup) < os.path.getmtime(of):
            raise GateRefusal("backup", f"backup {backup} is older than {of} — re-take it")
        return
    if staged is None:
        raise GateRefusal("backup", f"{BIN_DIR}/ops_apply_staged.py is not installed "
                                    f"(lumen-bin half of wq#4688) — cannot verify the backup")
    if m["node"] not in getattr(_bin_modules()[0], "APPLY_STAGED_LOCAL_NODES", ()):
        raise GateRefusal("backup", f"backup on node {m['node']!r} cannot be verified from "
                                    f"Lumen at spawn (apply-staged v1 is Lumen-local)")
    try:
        with open(m["patch"], "rb") as f:
            patch_bytes = f.read()
    except OSError as e:
        raise GateRefusal("backup", f"staged patch unreadable: {e}") from None
    if hashlib.sha256(patch_bytes).hexdigest() != m["patch_sha256"]:
        raise GateRefusal("backup", f"{m['patch']} no longer matches patch_sha256")
    try:
        staged.verify_backup(m["tree"], patch_bytes, m["backup"],
                             expected_patch_sha=m["patch_sha256"])
    except staged.StagedError as e:
        raise GateRefusal("backup", f"{e.code}: {e.message}") from None


# ---------------------------------------------------------------------------
# gate 5: target — node, unit, tree, docker argv against the static policy
# ---------------------------------------------------------------------------

def check_target(m, policy_path=None):
    node = m["node"]
    if node == THOR:
        raise GateRefusal("target", "node thor is THOR_HARDLOCK: never an ops-apply target")
    P, _ = _bin_modules()
    if P is None:
        raise GateRefusal("target", f"{BIN_DIR}/fleet_exec_policy.py missing")
    if node in P.THOR_HARDLOCK:
        raise GateRefusal("target", f"{node} is THOR_HARDLOCK")
    try:
        policy = P.load_policy(policy_path or DEFAULT_POLICY_PATH)
    except P.PolicyError as e:
        raise GateRefusal("target", f"fleet-exec-policy.yaml unreadable: {e}") from None
    if node in policy["never"].get("nodes", []):
        raise GateRefusal("target", f"{node} is in never.nodes")
    if m["kind"] == "apply":
        check = getattr(P, "check_apply_staged", None)
        if check is None:
            raise GateRefusal("target", "installed fleet_exec_policy has no check_apply_staged "
                                        "(lumen-bin half of wq#4688 not installed)")
        ok, rule, reason = check(node, m["tree"], policy)
        if not ok:
            raise GateRefusal("target", f"apply {m['tree']} denied by {rule}: {reason}")
        unit = m.get("restart")
        if unit:
            allow = (policy.get("allow", {}).get("restart") or {}).get(node) or []
            if not any(fnmatch.fnmatchcase(unit, g) for g in allow):
                raise GateRefusal("target", f"restart {unit} is not in allow.restart[{node}]={allow!r}")
            ok, rule, reason = P.check_restart(node, unit, policy)
            if not ok:
                raise GateRefusal("target", f"restart {unit}@{node} denied by {rule}: {reason}")
    else:
        check = getattr(P, "check_docker_exec", None)
        if check is None:
            raise GateRefusal("target", "installed fleet_exec_policy has no check_docker_exec "
                                        "(lumen-bin half of wq#4688 not installed)")
        for key in ("docker_argv", "docker_inverse_argv"):
            ok, rule, reason = check(node, m["docker_container"], m[key], policy)
            if not ok:
                raise GateRefusal("target", f"{key} denied by {rule}: {reason}")
    if "postcheck_argv" in m:
        ok, rule, reason = P.check_run(node, m["postcheck_argv"], policy)
        if not ok:
            raise GateRefusal("target", f"postcheck_argv denied by {rule}: {reason}")


# ---------------------------------------------------------------------------
# the whole gate + the grant it buys
# ---------------------------------------------------------------------------

def evaluate(ticket_id, ticket, *, policy_path=None, judge_db=None):
    """Run gates 0-5 in order; return the manifest, or raise the FIRST GateRefusal."""
    m = parse_manifest(ticket.get("detail"))
    _, report_bytes = check_report(ticket, m)
    check_judge(ticket_id, binding(report_bytes, m), judge_db)
    check_precheck(ticket)
    check_backup(m)
    check_target(m, policy_path)
    return m


def grants(m):
    """fleet-exec grant entries for a manifest that passed evaluate(). Every operand is
    pinned; fleet-exec re-checks each against the static policy at call time."""
    spec = postcheck_spec(m)
    node = m["node"]
    if m["kind"] == "apply":
        out = [{"kind": "apply", "node": node, "tree": m["tree"], "patch": m["patch"],
                "patch_sha256": m["patch_sha256"], "backup": m["backup"],
                "restart": m.get("restart"), "postcheck": spec}]
        if m.get("restart"):
            out.append({"kind": "restart", "node": node, "unit": m["restart"]})
        return out
    return [{"kind": "docker_exec", "node": node, "container": m["docker_container"],
             "argv": list(m["docker_argv"]), "inverse_argv": list(m["docker_inverse_argv"]),
             "postcheck": spec}]


# ---------------------------------------------------------------------------
# orchestrator CLI: make the backup, print the judge binding, dry-run the gates
# ---------------------------------------------------------------------------

def _wq_show(ticket_id):
    import subprocess
    p = subprocess.run(["/home/lumen/bin/wq", "show", str(ticket_id), "--json"],
                       capture_output=True, text=True, timeout=60)
    try:
        t = json.loads(p.stdout)
    except ValueError:
        t = {}
    if not isinstance(t, dict) or t.get("error") or not t:
        raise SystemExit(f"wq show {ticket_id} failed: {(p.stderr or p.stdout)[:300]}")
    return t


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="ops_apply_lane.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup", help="back up every file a staged patch touches (orchestrator, "
                                      "before `wq edit --lane ops-apply`)")
    b.add_argument("--tree", required=True)
    b.add_argument("--patch", required=True)
    b.add_argument("--out", required=True, help="NEW directory, e.g. "
                   "/home/lumen/backups/ops-apply/wq<N>-<ts>")
    bd = sub.add_parser("binding", help="print the judge binding + judge command for a ticket")
    bd.add_argument("--ticket", required=True, type=int)
    c = sub.add_parser("check", help="dry-run gates 0-5 for a ticket (no spawn, no writes)")
    c.add_argument("--ticket", required=True, type=int)
    a = ap.parse_args(argv)
    if a.cmd == "backup":
        _, staged = _bin_modules()
        if staged is None:
            raise SystemExit(f"{BIN_DIR}/ops_apply_staged.py not installed")
        with open(a.patch, "rb") as f:
            patch_bytes = f.read()
        man = staged.make_backup(a.tree, patch_bytes, a.out)
        print(json.dumps({"backup": a.out, "patch_sha256": man["patch_sha256"],
                          "files": man["files"]}, indent=2, sort_keys=True))
        return 0
    t = _wq_show(a.ticket)
    if a.cmd == "binding":
        m = parse_manifest(t.get("detail"))
        _, data = check_report(t, m)
        bind = binding(data, m)
        print(f"{BINDING_PREFIX}{bind}")
        print("judge it (verdict must be `allow`):")
        print(f"  judge --actor wq{a.ticket} --kind diff --command \"$(cat "
              f"{shlex.quote(m.get('patch') or report_path(t))})\" "
              f"--context '{BINDING_PREFIX}{bind} report:{report_path(t)}'")
        return 0
    try:
        m = evaluate(a.ticket, t)
    except GateRefusal as e:
        print(f"REFUSED {e.code}: {e.summary}")
        return 1
    print(f"PASS all gates; grant would be:\n{json.dumps(grants(m), indent=2, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
