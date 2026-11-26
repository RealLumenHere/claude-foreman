"""wq#4688 — the ops-apply lane (design/orchestrator_verify_lane.md §3 option B + §6, §7
exit criterion for B):

  * T5 (wq#4624 shape, real `fworker run` subprocess against a scratch wq DB): the
    ticket is refused five times — once per gate, report -> judge -> precheck ->
    backup -> target — each an uncharged LAUNCH_FAILURE that leaves the ticket open
    with no attempt-N.json, and only then spawns once; the grant that spawn minted
    then drives a real `fleet-exec apply-staged` (bundle-patched copy of lumen-bin)
    to a verified apply.
  * a staged BROKEN patch that passes every gate is applied, fails its post-check,
    and is rolled back from the backup with the ticket blocked.
  * per-gate unit cases, the permission pack's shape, and the never-auto-dispatched
    lane entry.

The lumen-bin half (fleet-exec verbs, fleet_exec_policy checks, ops_apply_staged) is
not installed by this ticket: tests apply ops/wq4688-ops-apply-lane/lumen-bin.patch to a
copy of the live /home/lumen/bin files, so a drifted lumen-bin fails here loudly.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from importlib.machinery import SourceFileLoader

import pytest
import yaml

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(HARNESS_DIR)
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402
import ops_apply_lane as L  # noqa: E402

FWORKER = os.path.join(HARNESS_DIR, "fworker.py")
WQ_BIN = "/home/lumen/bin/wq"
LIVE_BIN = "/home/lumen/bin"
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
FAKE_CLAUDE = os.path.join(TESTS_DIR, "fake_claude.py")
FAKE_GOVERNOR = os.path.join(TESTS_DIR, "fake_governor.py")
BUNDLE = os.path.join(REPO, "ops", "wq4688-ops-apply-lane")
BIN_PATCH = os.path.join(BUNDLE, "lumen-bin.patch")
REAL_POLICY = os.path.join(HARNESS_DIR, "fleet-exec-policy.yaml")

SERVER_V1 = ('def wq_items(rows):\n'
             '    return [{"id": r["id"], "title": r["title"]} for r in rows]\n')
SERVER_V2 = ('def wq_items(rows):\n'
             '    return [{"id": r["id"], "title": r["title"],\n'
             '             "detail": (r["detail"] or "")[:1500]} for r in rows]\n')
SERVER_BROKEN = ('def wq_items(rows):\n'
                 '    return [{"id": r["id"]} for r in rows]  # drops title AND detail\n')
UNIT = "lumen-messenger"
PRECHECK_NOTE = ("precheck: 3 related prior episode(s) found (no recorded failures). "
                 "plan: apply staged messenger /wq detail patch + restart lumen-messenger")


# ---------------------------------------------------------------------------
# fixtures: bundle-patched lumen-bin copy, a messenger-shaped tree, scratch policy/judge
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def patched_bin(tmp_path_factory):
    d = tmp_path_factory.mktemp("lumen-bin")
    for name in ("fleet-exec", "fleet_exec_policy.py"):
        shutil.copy2(os.path.join(LIVE_BIN, name), d / name)
    live_staged = os.path.join(LIVE_BIN, "ops_apply_staged.py")
    if "def check_apply_staged" in (d / "fleet_exec_policy.py").read_text() \
            and os.path.exists(live_staged):
        shutil.copy2(live_staged, d / "ops_apply_staged.py")   # already installed
    else:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        includes = ["--include=fleet-exec", "--include=fleet_exec_policy.py",
                    "--include=ops_apply_staged.py"]
        chk = subprocess.run(["git", "apply", "--check", *includes, BIN_PATCH], cwd=d,
                             capture_output=True, text=True)
        assert chk.returncode == 0, ("lumen-bin.patch no longer applies to live "
                                     f"/home/lumen/bin (drift): {chk.stderr}")
        subprocess.run(["git", "apply", *includes, BIN_PATCH], cwd=d, check=True)
    return str(d)


@pytest.fixture
def lane(patched_bin, monkeypatch):
    L.use_bin_dir(patched_bin)
    yield
    L.use_bin_dir(LIVE_BIN)


def load_patched_fleet_exec(patched_bin):
    """fleet-exec imports fleet_exec_policy/ops_apply_staged by name; fworker already
    holds the LIVE fleet_exec_policy in sys.modules, so swap the names for the load."""
    saved = {k: sys.modules.pop(k) for k in ("fleet_exec_policy", "ops_apply_staged")
             if k in sys.modules}
    sys.path.insert(0, patched_bin)
    try:
        loader = SourceFileLoader("fleet_exec_wq4688", os.path.join(patched_bin, "fleet-exec"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(mod)
    finally:
        sys.path.remove(patched_bin)
        for k in ("fleet_exec_policy", "ops_apply_staged"):
            sys.modules.pop(k, None)
        sys.modules.update(saved)
    assert hasattr(mod.P, "check_apply_staged")
    return mod


def git(tree, *args):
    return subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True,
                          check=True)


def make_messenger_tree(root):
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@lumen")
    git(root, "config", "user.name", "t")
    (root / "server_v2.py").write_text(SERVER_V1)
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "init")
    return root


def stage_patch(tree, new_text, out):
    (tree / "server_v2.py").write_text(new_text)
    out.write_text(git(tree, "diff").stdout)
    git(tree, "checkout", "-q", "--", ".")
    return out


def scratch_policy(path, tree, restart_allowed):
    real = yaml.safe_load(open(REAL_POLICY))
    pol = {"never": real["never"],          # crown jewels exactly as shipped
           "allow": {"restart": {"lumen": [UNIT] if restart_allowed else []},
                     "apply_staged": {"lumen": [str(tree)]},
                     "docker_exec": {"lumen": {"ntfy": ["ntfy access"]}}},
           "run_readonly_cmds": ["true", "cat"]}
    path.write_text(yaml.safe_dump(pol))
    return str(path)


def make_judge_db(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE judge_verdicts(
      ts TEXT NOT NULL, actor TEXT, action_kind TEXT, command TEXT,
      context TEXT, input_gate_verdict TEXT, model TEXT, model_source TEXT,
      tier TEXT, verdict TEXT, conditions TEXT, rationale TEXT,
      latency_s REAL, fail_closed INT NOT NULL DEFAULT 0, error TEXT)""")
    conn.commit()
    conn.close()
    return str(path)


def judge(db, ticket_id, bind, verdict="allow", ts="2026-11-26T07:00:00Z", fail_closed=0):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO judge_verdicts (ts, actor, action_kind, command, context, "
                 "verdict, fail_closed) VALUES (?,?,?,?,?,?,?)",
                 (ts, f"wq{ticket_id}", "diff", "(patch)",
                  f"{L.BINDING_PREFIX}{bind} report:x", verdict, fail_closed))
    conn.commit()
    conn.close()


def manifest_text(tree, patch, backup, *, unit=UNIT, node="lumen"):
    sha = hashlib.sha256(patch.read_bytes()).hexdigest()
    lines = [f"node: {node}", f"tree: {tree}", f"patch: {patch}", f"patch_sha256: {sha}",
             f"backup: {backup}"]
    if unit:
        lines.append(f"restart: {unit}")
    lines += [f"postcheck_argv: cat {tree}/server_v2.py", 'postcheck_expect: "detail"']
    return ("[messenger] /wq items: add detail (wq#4624 shape)\n\n```ops-apply\n"
            + "\n".join(lines) + "\n```\n")


def write_report(path, patch, unit=UNIT):
    sha = hashlib.sha256(patch.read_bytes()).hexdigest()
    path.write_text(f"# verify report\nstaged diff sha256 {sha}\nrestart {unit}\n"
                    f"probe: GET /wq items carry detail -> currently ABSENT\n")
    return str(path)


@pytest.fixture
def scene(tmp_path, lane, patched_bin, monkeypatch):
    tree = make_messenger_tree(tmp_path / "messenger")
    patch = stage_patch(tree, SERVER_V2, tmp_path / "wq4624.patch")
    backup = tmp_path / "backups" / "wq4624"
    db = make_judge_db(tmp_path / "minerva.db")
    policy = scratch_policy(tmp_path / "policy.yaml", tree, restart_allowed=True)
    monkeypatch.setattr(L, "JUDGE_DB", db)
    monkeypatch.setattr(fworker, "FLEET_EXEC_POLICY_PATH", policy)
    return {"tmp": tmp_path, "tree": tree, "patch": patch, "backup": backup, "db": db,
            "policy": policy, "bin": patched_bin}


def passing_ticket(scene, ticket_id=4624, patch=None):
    patch = patch or scene["patch"]
    S = L._bin_modules()[1]
    if not scene["backup"].exists():
        S.make_backup(str(scene["tree"]), patch.read_bytes(), str(scene["backup"]))
    report = write_report(scene["tmp"] / "report.md", patch)
    t = {"lane": "ops-apply", "detail": manifest_text(scene["tree"], patch, scene["backup"]),
         "gate": report, "notes": PRECHECK_NOTE}
    m = L.parse_manifest(t["detail"])
    judge(scene["db"], ticket_id, L.binding(open(report, "rb").read(), m))
    return t


# ---------------------------------------------------------------------------
# gate-by-gate (in-process, through fworker.assert_ops_apply_gates)
# ---------------------------------------------------------------------------

def refusal(ticket_id, ticket):
    with pytest.raises(fworker.SpawnRefused) as e:
        fworker.assert_ops_apply_gates(ticket_id, ticket)
    return e.value


def test_other_lanes_are_untouched():
    assert fworker.assert_ops_apply_gates(1, {"lane": "build", "detail": "x"}) is None


def test_all_gates_pass_and_grant_pins_every_operand(scene):
    t = passing_ticket(scene)
    grants = fworker.assert_ops_apply_gates(4624, t)
    apply = next(g for g in grants if g["kind"] == "apply")
    assert apply["patch_sha256"] == hashlib.sha256(scene["patch"].read_bytes()).hexdigest()
    assert apply["backup"] == str(scene["backup"]) and apply["restart"] == UNIT
    assert apply["postcheck"] == {"argv": ["cat", f"{scene['tree']}/server_v2.py"],
                                  "expect": '"detail"'}
    assert {"kind": "restart", "node": "lumen", "unit": UNIT} in grants


@pytest.mark.parametrize("detail,needle", [
    ("no block at all", "exactly one ```ops-apply block"),
    ("```ops-apply\nnode: lumen\n```\n```ops-apply\nnode: lumen\n```", "found 2"),
    ("```ops-apply\nnode: lumen\nbogus: 1\n```", "unknown manifest line"),
    ("```ops-apply\nnode: lumen\nnode: isis\n```", "given twice"),
    ("```ops-apply\nnode: lumen\npostcheck_http: http://x\npostcheck_expect: y\n```",
     "exactly one mutation"),
    ("```ops-apply\nnode: lumen\ntree: /t\ndocker_container: ntfy\n```", "exactly one mutation"),
    ("```ops-apply\nnode: lumen\ntree: /t\npatch: /p\n```", "missing patch_sha256, backup"),
    ("```ops-apply\nnode: lumen\ntree: t\npatch: /p\n```", "normalized absolute path"),
])
def test_manifest_refusals(detail, needle):
    e = refusal(1, {"lane": "ops-apply", "detail": detail})
    assert e.code == "OPS_APPLY_MANIFEST_INVALID" and needle in e.summary


def test_manifest_requires_postcheck(scene):
    t = passing_ticket(scene)
    t["detail"] = t["detail"].replace('postcheck_expect: "detail"\n', "")
    assert refusal(4624, t).code == "OPS_APPLY_MANIFEST_INVALID"


def test_report_must_name_the_diff_and_unit(scene):
    t = passing_ticket(scene)
    open(t["gate"], "w").write("# report that names nothing\n")
    e = refusal(4624, t)
    assert e.code == "GATE_MISSING" and "does not name" in e.summary


def test_editing_the_report_after_judging_voids_the_pass(scene):
    t = passing_ticket(scene)
    with open(t["gate"], "a") as f:
        f.write("one more line after the judge said allow\n")
    assert refusal(4624, t).code == "JUDGE_NOT_PASS"


def test_editing_the_manifest_after_judging_voids_the_pass(scene):
    t = passing_ticket(scene)
    t["detail"] = t["detail"].replace(f"restart: {UNIT}\n", "")
    assert refusal(4624, t).code == "JUDGE_NOT_PASS"


@pytest.mark.parametrize("verdict,fail_closed", [
    ("deny", 0), ("allow-with-conditions", 0), (None, 1), ("allow", 1)])
def test_newest_bound_verdict_must_be_plain_allow(scene, verdict, fail_closed):
    t = passing_ticket(scene)
    m = L.parse_manifest(t["detail"])
    bind = L.binding(open(t["gate"], "rb").read(), m)
    judge(scene["db"], 4624, bind, verdict=verdict, ts="2026-11-26T08:00:00Z",
          fail_closed=fail_closed)
    assert refusal(4624, t).code == "JUDGE_NOT_PASS"


def test_a_verdict_for_another_ticket_does_not_count(scene):
    t = passing_ticket(scene, ticket_id=9999)
    assert refusal(4624, t).code == "JUDGE_NOT_PASS"


def test_unreadable_judge_db_refuses(scene, monkeypatch):
    t = passing_ticket(scene)
    monkeypatch.setattr(L, "JUDGE_DB", str(scene["tmp"] / "nope.db"))
    assert refusal(4624, t).code == "JUDGE_NOT_PASS"


def test_precheck_must_be_in_notes(scene):
    t = passing_ticket(scene)
    t["notes"] = "precheck: I thought about it"
    assert refusal(4624, t).code == "PRECHECK_MISSING"


@pytest.mark.parametrize("note", [
    "⚠️  2 related past failure/lesson(s) found -- review before acting.",
    "No prior episodes on this. Proceed, then commit a post-mortem."])
def test_every_precheck_verdict_shape_counts(scene, note):
    t = passing_ticket(scene)
    t["notes"] = note
    assert fworker.assert_ops_apply_gates(4624, t)


def test_backup_of_a_stale_state_refuses(scene):
    t = passing_ticket(scene)
    (scene["tree"] / "server_v2.py").write_text(SERVER_V1 + "# hand edit after backup\n")
    e = refusal(4624, t)
    assert e.code == "BACKUP_MISSING" and "BACKUP_STALE" in e.summary


def test_patch_swapped_after_judging_refuses(scene):
    t = passing_ticket(scene)
    scene["patch"].write_text(scene["patch"].read_text().replace("1500", "9999"))
    e = refusal(4624, t)
    assert e.code == "BACKUP_MISSING" and "patch_sha256" in e.summary


def test_bin_half_not_installed_refuses_backup_gate(scene, tmp_path):
    t = passing_ticket(scene)
    bare = tmp_path / "bare-bin"
    bare.mkdir()
    shutil.copy2(os.path.join(LIVE_BIN, "fleet_exec_policy.py"), bare / "fleet_exec_policy.py")
    L.use_bin_dir(str(bare))
    if "def check_apply_staged" in (bare / "fleet_exec_policy.py").read_text():
        pytest.skip("lumen-bin half already installed")
    e = refusal(4624, t)
    assert e.code == "BACKUP_MISSING" and "not installed" in e.summary


@pytest.mark.parametrize("mutate,needle", [
    ("restart_not_allowed", "not in allow.restart"),
    ("tree_not_allowed", "apply_tree_not_allowed"),
    ("postcheck_not_readonly", "not_readonly"),
])
def test_target_gate(scene, mutate, needle):
    t = passing_ticket(scene)
    pol = yaml.safe_load(open(scene["policy"]))
    if mutate == "restart_not_allowed":
        pol["allow"]["restart"]["lumen"] = []
    elif mutate == "tree_not_allowed":
        pol["allow"]["apply_staged"]["lumen"] = ["/home/lumen/messenger"]
    elif mutate == "postcheck_not_readonly":
        pol["run_readonly_cmds"] = ["true"]
    open(scene["policy"], "w").write(yaml.safe_dump(pol))
    e = refusal(4624, t)
    assert e.code == "CAPABILITY_DENIED_BY_POLICY" and needle in e.summary


def test_crown_jewel_unit_refused_even_if_listed_in_allow(scene):
    pol = yaml.safe_load(open(scene["policy"]))
    pol["allow"]["restart"]["lumen"] = ["*"]
    open(scene["policy"], "w").write(yaml.safe_dump(pol))
    m = L.parse_manifest(manifest_text(scene["tree"], scene["patch"], scene["backup"],
                                       unit="cloudflared"))
    with pytest.raises(L.GateRefusal) as e:
        L.check_target(m, scene["policy"])
    assert "never_restart" in e.value.summary


def test_thor_is_refused_before_policy_is_read(scene):
    m = L.parse_manifest(manifest_text(scene["tree"], scene["patch"], scene["backup"],
                                       node="thor"))
    with pytest.raises(L.GateRefusal) as e:
        L.check_target(m, "/nonexistent/policy.yaml")
    assert e.value.code == "CAPABILITY_DENIED_BY_POLICY" and "THOR_HARDLOCK" in e.value.summary


def docker_ticket(scene, argv="ntfy access everyone ccc rw",
                  inverse="ntfy access --reset everyone ccc"):
    auth = scene["tmp"] / "auth.db"
    auth.write_text("acl")
    bk = scene["tmp"] / "auth.db.bak"
    shutil.copy2(auth, bk)
    os.utime(bk, (os.path.getmtime(auth) + 5,) * 2)
    detail = ("```ops-apply\nnode: lumen\ndocker_container: ntfy\n"
              f"docker_argv: {argv}\ndocker_inverse_argv: {inverse}\n"
              f"backup: {bk}\nbackup_of: {auth}\n"
              "postcheck_http: http://100.117.66.102:8080/ccc/json?poll=1\n"
              "postcheck_expect: ccc\n```\n")
    report = scene["tmp"] / "report-ntfy.md"
    report.write_text(f"verify: ntfy {argv} needed\n")
    t = {"lane": "ops-apply", "detail": detail, "gate": str(report), "notes": PRECHECK_NOTE}
    judge(scene["db"], 4619, L.binding(report.read_bytes(), L.parse_manifest(detail)))
    return t


def test_docker_exec_manifest_passes_and_pins_argv_and_inverse(scene):
    grants = fworker.assert_ops_apply_gates(4619, docker_ticket(scene))
    assert grants == [{"kind": "docker_exec", "node": "lumen", "container": "ntfy",
                       "argv": ["ntfy", "access", "everyone", "ccc", "rw"],
                       "inverse_argv": ["ntfy", "access", "--reset", "everyone", "ccc"],
                       "postcheck": {"http": "http://100.117.66.102:8080/ccc/json?poll=1",
                                     "expect": "ccc"}}]


def test_docker_exec_outside_the_allowlist_refused(scene):
    t = docker_ticket(scene, argv="ntfy user add --role=admin x")
    e = refusal(4619, t)
    assert e.code == "CAPABILITY_DENIED_BY_POLICY" and "docker_exec_not_allowed" in e.summary


def test_docker_backup_older_than_target_refused(scene):
    t = docker_ticket(scene)
    m = L.parse_manifest(t["detail"])
    os.utime(m["backup"], (os.path.getmtime(m["backup_of"]) - 60,) * 2)
    assert refusal(4619, t).code == "BACKUP_MISSING"


# ---------------------------------------------------------------------------
# the lane's permission pack, settings routing, and dispatcher entry
# ---------------------------------------------------------------------------

def test_manifest_operands_are_not_worker_read_citations():
    detail = ("ship it\n```ops-apply\nnode: lumen\ntree: /home/lumen/messenger\n"
              "backup: /home/lumen/backups/ops-apply/wq1\n```\nsee /home/lumen/kb/x.md\n")
    stripped = L.strip_manifest(detail)
    assert "/home/lumen/backups" not in stripped and "/home/lumen/messenger" not in stripped
    assert "/home/lumen/kb/x.md" in stripped


@pytest.mark.parametrize("model,runtime", [("claude-sonnet-5", "sdk"),
                                           ("claude-sonnet-5", "aider")])
def test_non_claude_runtimes_are_refused(scene, monkeypatch, model, runtime):
    t = passing_ticket(scene)
    monkeypatch.setattr(fworker, "wq_show", lambda tid: t)
    args = fworker.argparse.Namespace(ticket=4624, model=model, archetype="mechanical-edit",
                                      runtime=runtime)
    with pytest.raises(fworker.SpawnRefused) as e:
        fworker.do_attempt(args, 1)
    assert e.value.code == "OPS_APPLY_RUNTIME"


def test_settings_routing_is_by_lane_and_wins_over_archetype():
    for arch in ("verify-probe", "standard-engineering", None):
        assert fworker.resolve_worker_settings("/home/lumen/foreman", 1, archetype=arch,
                                               lane="ops-apply") == L.SETTINGS_FILE


def test_missing_profile_refuses_instead_of_falling_back(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "SETTINGS_FILE", str(tmp_path / "gone.json"))
    with pytest.raises(fworker.SpawnRefused) as e:
        fworker.resolve_worker_settings("/home/lumen/foreman", 1, lane="ops-apply")
    assert e.value.code == "OPS_APPLY_PROFILE_MISSING"
    with pytest.raises(fworker.SpawnRefused):
        fworker.assert_ops_apply_gates(1, {"lane": "ops-apply", "detail": ""})


def test_permission_pack_shape():
    pack = json.load(open(L.SETTINGS_FILE))
    allow, deny = pack["permissions"]["allow"], pack["permissions"]["deny"]
    assert "Write" not in allow and "Edit" not in allow
    assert {"Write(reports/**)", "Edit(reports/**)"} <= set(allow)
    mutation_paths = [a for a in allow if "fleet-exec" in a]
    assert set(mutation_paths) == {
        "Bash(/home/lumen/bin/fleet-exec apply-staged *)",
        "Bash(/home/lumen/bin/fleet-exec docker-exec-allowlisted *)",
        "Bash(/home/lumen/bin/fleet-exec run *)",
        "Bash(/home/lumen/bin/fleet-exec grant)"}
    for forbidden in ("Bash(git *)", "Bash(python3 *)", "Bash(sed *)", "Bash(awk *)",
                      "Bash(find *)", "Bash(cp *)", "Bash(curl *)", "Bash(docker ps*)",
                      "Bash(/home/lumen/bin/lumen-run *)", "Bash(/home/lumen/bin/fleet-exec *)"):
        assert forbidden not in allow, forbidden
    for must in ("Bash(git apply*)", "Bash(git push*)", "Bash(docker *)", "Bash(sudo *)",
                 "Bash(systemctl restart*)", "Bash(/home/lumen/bin/lumen-run *)",
                 "Bash(/home/lumen/bin/fleet-exec restart *)", "Read(/home/lumen/.secrets/**)",
                 "Write(/home/lumen/messenger/**)", "Write(/home/lumen/backups/**)"):
        assert must in deny, must
    for group in pack["hooks"].values():
        for entry in group:
            for h in entry["hooks"]:
                script = [w for w in h["command"].split() if w.startswith("/home/lumen/foreman/")][0]
                rel = os.path.relpath(script, "/home/lumen/foreman")
                assert os.path.exists(os.path.join(REPO, rel)), script


def test_lane_is_configured_but_never_auto_dispatched():
    cfg = json.load(open(os.path.join(REPO, "dispatcher", "config.json")))
    assert cfg["lanes"]["ops-apply"]["enabled"] is False
    sys.path.insert(0, os.path.join(REPO, "dispatcher"))
    import foreman_dispatcher
    scanned = []
    orig = foreman_dispatcher._scan_lane
    try:
        foreman_dispatcher._scan_lane = lambda lane, log: scanned.append(lane) or []
        foreman_dispatcher.scan_candidates({"lanes": {"ops-apply": cfg["lanes"]["ops-apply"],
                                                      "build": {"enabled": True}}}, print)
    finally:
        foreman_dispatcher._scan_lane = orig
    assert scanned == ["build"]


def test_shipped_policy_keeps_crown_jewels():
    pol = yaml.safe_load(open(REAL_POLICY))
    assert "thor" in pol["never"]["nodes"]
    assert {"vaultwarden*", "cloudflared", "desktop-commander-mcp", "postgresql*",
            "tailscaled", "sshd"} <= set(pol["never"]["restart"]["lumen"])
    assert pol["never"]["restart"]["maat"] == ["*"]
    assert pol["allow"]["apply_staged"] == {"lumen": ["/home/lumen/messenger"]}
    assert pol["allow"]["docker_exec"] == {"lumen": {"ntfy": ["ntfy access"]}}


# ---------------------------------------------------------------------------
# apply through the pinned grant (patched fleet-exec), success and auto-rollback
# ---------------------------------------------------------------------------

def run_broker(fe, monkeypatch, tmp, grant_path, ticket_id, argv):
    calls = {"restart": [], "wq": [], "ntfy": []}
    raw = open(grant_path, "rb").read()
    monkeypatch.setenv("FOREMAN_GRANT_FILE", grant_path)
    monkeypatch.setenv("FOREMAN_GRANT_SHA", hashlib.sha256(raw).hexdigest())
    monkeypatch.setenv("FOREMAN_ATTEMPT", "1")
    monkeypatch.setenv("WQ_AGENT", f"fworker-wq{ticket_id}")
    monkeypatch.setattr(fe, "AUDIT_PATH", str(tmp / "fleet-exec.jsonl"))
    monkeypatch.setattr(fe, "WORKER_AUDIT_PATH", None)
    monkeypatch.setattr(fe, "POSTCHECK_BUDGET_S", 0)
    monkeypatch.setattr(fe, "known_nodes", lambda: {"lumen", "thor"})
    monkeypatch.setattr(fe, "restart_via_broker",
                        lambda n, u: (calls["restart"].append(u), (0, "active"))[1])
    monkeypatch.setattr(fe, "wq_block", lambda t, r: (calls["wq"].append((t, r)), (0, "ok"))[1])
    monkeypatch.setattr(fe, "notify", lambda *a: calls["ntfy"].append(a))
    monkeypatch.setattr(fe, "run_ssh", lambda node, a, **kw: subprocess.run(
        a, capture_output=True, text=True))        # postcheck `cat` runs locally
    with pytest.raises(SystemExit) as e:
        fe.main(argv)
    audit = [json.loads(x) for x in open(tmp / "fleet-exec.jsonl")]
    return e.value.code, calls, audit


def mint_grant(scene, ticket_id, ticket, monkeypatch):
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(scene["tmp"] / "_runs"))
    extra = fworker.assert_ops_apply_gates(ticket_id, ticket)
    _, _, path = fworker.resolve_exec_grant(ticket_id, 1, fworker.needs_mod.Needs([]), 600,
                                            extra_grants=extra)
    return path


def test_staged_broken_patch_auto_rolls_back(scene, monkeypatch):
    """§7 exit criterion for B: break the patch on purpose; every gate passes (the gates
    judge evidence, not outcomes), the broker applies it, the post-check catches it, the
    backup is restored, the unit is restarted onto the restored code, the ticket blocked."""
    broken = stage_patch(scene["tree"], SERVER_BROKEN, scene["tmp"] / "broken.patch")
    t = passing_ticket(scene, patch=broken)
    grant_path = mint_grant(scene, 4624, t, monkeypatch)
    fe = load_patched_fleet_exec(scene["bin"])
    monkeypatch.setattr(fe, "POLICY_PATH", scene["policy"])
    code, calls, audit = run_broker(fe, monkeypatch, scene["tmp"], grant_path, 4624,
                                    ["apply-staged", "lumen", str(scene["tree"])])
    assert code == fe.EXIT_ROLLED_BACK
    assert (scene["tree"] / "server_v2.py").read_text() == SERVER_V1
    assert git(scene["tree"], "status", "--porcelain").stdout == ""
    assert calls["restart"] == [UNIT, UNIT]
    (ticket, reason), = calls["wq"]
    assert ticket == "4624" and "ops-apply:postcheck-failed (rolled-back)" in reason
    assert audit[-1]["rule"] == "apply:rolled_back" and audit[-1]["verified"] is False


def test_apply_with_a_grant_the_lane_did_not_mint_is_denied(scene, monkeypatch):
    t = passing_ticket(scene)
    grant_path = mint_grant(scene, 4624, t, monkeypatch)
    fe = load_patched_fleet_exec(scene["bin"])
    monkeypatch.setattr(fe, "POLICY_PATH", scene["policy"])
    code, calls, audit = run_broker(fe, monkeypatch, scene["tmp"], grant_path, 4624,
                                    ["apply-staged", "lumen", "/home/lumen/messenger"])
    assert code == 2 and audit[-1]["rule"] == "grant:no_apply_tree"
    assert (scene["tree"] / "server_v2.py").read_text() == SERVER_V1


# ---------------------------------------------------------------------------
# T5 — the wq#4624 shape through a REAL `fworker run`: 5 refusals, then 1 spawn
# ---------------------------------------------------------------------------

def test_t5_refuses_once_per_gate_then_runs_once_then_applies(scene, tmp_path, monkeypatch):
    # gate 5 starts failing: lumen-messenger is not (yet) in this policy's allow.restart.
    scratch_policy(tmp_path / "policy.yaml", scene["tree"], restart_allowed=False)
    repo = tmp_path / "repo"
    make_messenger_tree(repo)
    git(repo, "remote", "add", "origin", "http://git.lumen/messenger.git")
    runs_root = tmp_path / "_runs"
    env = dict(os.environ)
    env.update({
        "WQ_DB": str(tmp_path / "scratch_wq.db"),
        "WQ_AGENT": "test-ops-apply-orchestrator",
        "WQ_DISPATCHER_CONFIG": os.path.join(REPO, "dispatcher", "config.json"),
        "FOREMAN_EVENTS_DIR": str(tmp_path / "events"),
        "FOREMAN_RUNS_ROOT": str(runs_root),
        "FOREMAN_WORKTREES_ROOT": str(tmp_path / "worktrees"),
        "FOREMAN_STOP_FILE": str(tmp_path / "STOP_nonexistent"),
        "FOREMAN_STATE_FILE": str(tmp_path / "state.json"),
        "FOREMAN_GOVERNOR_CLI": FAKE_GOVERNOR,
        "FOREMAN_WQ_BIN": WQ_BIN,
        "FOREMAN_NTFY_BIN": "/bin/true",
        "FOREMAN_NO_REFILL": "1",
        "FOREMAN_FLEET_EXEC_POLICY": scene["policy"],
        "FOREMAN_OPS_APPLY_BIN_DIR": scene["bin"],
        "FOREMAN_OPS_APPLY_JUDGE_DB": scene["db"],
    })
    os.makedirs(env["FOREMAN_EVENTS_DIR"], exist_ok=True)

    def wq(*args):
        p = subprocess.run([WQ_BIN, *args], capture_output=True, text=True, env=env)
        assert p.returncode == 0, p.stdout + p.stderr
        return p.stdout

    detail = manifest_text(scene["tree"], scene["patch"], scene["backup"])
    out = wq("add", "--title", "[messenger] /wq items: add detail (T5, wq#4624 shape)",
             "--lane", "ops-apply", "--detail", detail)
    tid = int(out.strip().split()[2])
    run_dir = runs_root / f"wq{tid}"

    def spawn():
        return subprocess.run(
            [sys.executable, FWORKER, "run", "--ticket", str(tid), "--repo", str(repo),
             "--model", "claude-sonnet-5", "--effort", "high", "--archetype", "mechanical-edit",
             "--claude-bin", FAKE_CLAUDE, "--worker-id", f"test-fworker-wq{tid}"],
            capture_output=True, text=True, env=env, timeout=180)

    def assert_refused(code):
        r = spawn()
        assert r.returncode == fworker.EXIT_LAUNCH_REFUSED, r.stdout + r.stderr
        assert code in r.stdout, r.stdout
        row = json.loads(wq("show", str(tid), "--json"))
        assert row["status"] == "open"                                   # released
        assert not [f for f in os.listdir(run_dir) if f.startswith("attempt-")]  # uncharged
        assert json.load(open(run_dir / "failure.json"))["classification"] == "LAUNCH_FAILURE"

    report = write_report(tmp_path / "verify-wq4624.md", scene["patch"])
    refusals = []

    assert_refused("GATE_MISSING")                                       # 1: no gate: report
    refusals.append("report")
    wq("edit", str(tid), "--gate", report)

    assert_refused("JUDGE_NOT_PASS")                                     # 2: not judged
    refusals.append("judge")
    t = json.loads(wq("show", str(tid), "--json"))
    judge(scene["db"], tid, L.binding(open(report, "rb").read(), L.parse_manifest(t["detail"])))

    assert_refused("PRECHECK_MISSING")                                   # 3: no precheck
    refusals.append("precheck")
    wq("note", str(tid), PRECHECK_NOTE)

    assert_refused("BACKUP_MISSING")                                     # 4: no backup
    refusals.append("backup")
    L._bin_modules()[1].make_backup(str(scene["tree"]), scene["patch"].read_bytes(),
                                    str(scene["backup"]))

    assert_refused("CAPABILITY_DENIED_BY_POLICY")                        # 5: unit not allowed
    refusals.append("target")
    scratch_policy(tmp_path / "policy.yaml", scene["tree"], restart_allowed=True)

    assert refusals == list(L.GATES[1:])                                 # one per gate, in order

    r = spawn()                                                          # ...then runs ONCE
    assert r.returncode != fworker.EXIT_LAUNCH_REFUSED, r.stdout + r.stderr
    assert "ops-apply gates 0-5 PASS" in (r.stdout + r.stderr)
    # it ran: the first CHARGED attempt is attempt-1 (the five refusals cost nothing).
    # The fake worker's `wq done` has no landed evidence, so fworker's own evidence gate
    # may re-attempt in-process (FALSE_COMPLETE) — every such attempt re-ran gates 0-5.
    attempts = sorted(f for f in os.listdir(run_dir) if f.startswith("attempt-"))
    assert attempts and attempts[0] == "attempt-1.json", r.stdout[-3000:] + r.stderr[-3000:]
    classes = [json.load(open(run_dir / a)).get("classification") for a in attempts]
    assert "LAUNCH_FAILURE" not in classes, classes
    assert (r.stdout + r.stderr).count("ops-apply gates 0-5 PASS") == len(attempts)
    grant_path = str(run_dir / "grant.json")
    grant = json.load(open(grant_path))
    assert {g["kind"] for g in grant["grants"]} == {"apply", "restart"}

    # the worker's one mutation: apply-staged through the grant that spawn minted
    fe = load_patched_fleet_exec(scene["bin"])
    monkeypatch.setattr(fe, "POLICY_PATH", scene["policy"])
    code, calls, audit = run_broker(fe, monkeypatch, tmp_path, grant_path, tid,
                                    ["apply-staged", "lumen", str(scene["tree"])])
    assert code == 0, audit[-1]
    assert (scene["tree"] / "server_v2.py").read_text() == SERVER_V2
    assert calls["restart"] == [UNIT] and calls["wq"] == []
    assert audit[-1]["rule"] == "apply:postcheck_ok" and audit[-1]["verified"] is True

    # and any later spawn of this ticket refuses: the backup no longer describes the
    # live tree, so a successful apply can never be re-applied by a retry.
    # (the fake worker's `wq done --notes` replaced the notes column, so re-record the
    # precheck the way an orchestrator re-entering the lane would; gate 4 still refuses.)
    t = dict(json.loads(wq("show", str(tid), "--json")), notes=PRECHECK_NOTE)
    e = refusal(tid, t)
    assert e.code == "BACKUP_MISSING" and "BACKUP_STALE" in e.summary
