"""wq#4830 — hermetic fworker output contract.

A worker's file output goes to its worktree or to /scratch/agent-work/<ticket-id>/ and
nowhere else. Three layers are covered here, each against the real thing:

  * guard.sh + hermetic.py hook: the PreToolUse envelope on stdin, exit code asserted
  * hermetic.py units: the writable set, modes, the Bash destination parser
  * the mount layer: real bubblewrap, real kernel refusals (skipped where bwrap or
    unprivileged user namespaces are unavailable)

Zero quota: no `claude -p` is spawned.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

HARNESS = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, HARNESS)
import hermetic  # noqa: E402

GUARD = os.path.join(HARNESS, "guard.sh")
FOREMAN = "/home/lumen/foreman"
SECRETS = "/home/lumen/.secrets"


@pytest.fixture
def box(tmp_path):
    """A ticket's three writable dirs plus one dir that is nobody's."""
    d = {k: tmp_path / k for k in ("worktree", "out", "scratch", "elsewhere", "events")}
    for p in d.values():
        p.mkdir()
    return {k: str(v) for k, v in d.items()}


def _env(box, **extra):
    env = dict(os.environ)
    for k in ("FOREMAN_HERMETIC", "FOREMAN_HERMETIC_BASH", "FOREMAN_HERMETIC_MOUNT",
              "FOREMAN_HERMETIC_EXTRA_WRITE", "FOREMAN_FIRE_PLAN_PATH", "FOREMAN_FIRE_PHASE",
              "FOREMAN_PRIMARY_TREES"):
        env.pop(k, None)
    env.update({"FOREMAN_WORKER": "test-wq4830", "FOREMAN_EVENTS_DIR": box["events"],
                "FOREMAN_WORKTREE_ROOT": box["worktree"], "FOREMAN_OUTPUT_DIR": box["out"],
                "FOREMAN_SCRATCH_DIR": box["scratch"], "FOREMAN_JUDGE_SHADOW": "0"})
    env.update(extra)
    return env


def guard(box, tool, tool_input, guard_path=GUARD, **extra):
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input,
                          "session_id": "t-wq4830", "cwd": box["worktree"]})
    p = subprocess.run([guard_path], input=payload, capture_output=True, text=True,
                       env=_env(box, **extra))
    return p.returncode, p.stderr


def write(box, path, tool="Write", **extra):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return guard(box, tool, {key: path, "content": "x", "old_string": "a",
                             "new_string": "b"}, **extra)


def bash(box, cmd, **extra):
    return guard(box, "Bash", {"command": cmd}, **extra)


def events(box):
    rows = []
    for name in os.listdir(box["events"]):
        if name.endswith(".jsonl"):
            with open(os.path.join(box["events"], name)) as f:
                rows += [json.loads(line) for line in f if line.strip()]
    return rows


# ---- Write / Edit / NotebookEdit: the allowlist ------------------------------------

@pytest.mark.parametrize("root", ["worktree", "out", "scratch"])
def test_write_inside_the_writable_set_is_allowed(box, root):
    assert write(box, os.path.join(box[root], "sub", "a.txt"))[0] == 0


@pytest.mark.parametrize("target", [
    "/home/lumen/wq4830_probe.txt",                  # home root
    "/home/lumen/.bashrc",                           # runs in every lumen shell
    "/home/lumen/.config/systemd/user/x.service",    # persistence
    "/home/lumen/worktrees/wq9999-a1/x.py",          # another ticket's worktree
    "/etc/wq4830_probe",
    "/scratch/agent-work/9999999/x.txt",             # another ticket's output dir
    "/tmp/wq4830_probe.txt",                         # bare /tmp, not the scratch grant
])
def test_write_outside_the_writable_set_is_refused(box, target):
    rc, err = write(box, target)
    assert rc == 2
    assert "hermetic output contract (wq#4830)" in err
    assert box["out"] in err                         # the reason says where to write


def test_write_to_unowned_dir_is_refused(box):
    rc, err = write(box, os.path.join(box["elsewhere"], "a.txt"))
    assert rc == 2 and "outside this ticket's writable set" in err


def test_dotdot_out_of_the_worktree_into_a_primary_tree_is_refused(box):
    # The wq#1719 wall matched the raw string, so this spelling went through.
    target = os.path.join(box["worktree"], *[".."] * 12, "home/lumen/foreman/harness/zz.py")
    rc, err = write(box, target)
    assert rc == 2 and "PRIMARY tree" in err


def test_symlink_inside_the_worktree_does_not_widen_the_set(box):
    link = os.path.join(box["worktree"], "escape")
    os.symlink(box["elsewhere"], link)
    assert write(box, os.path.join(link, "a.txt"))[0] == 2
    os.symlink(FOREMAN, os.path.join(box["worktree"], "live"))
    rc, err = write(box, os.path.join(box["worktree"], "live", "harness", "zz.py"))
    assert rc == 2 and "PRIMARY tree" in err


def test_edit_is_held_to_the_same_set(box):
    assert write(box, os.path.join(box["worktree"], "a.py"), tool="Edit")[0] == 0
    assert write(box, "/home/lumen/.bashrc", tool="Edit")[0] == 2


def test_notebookedit_is_held_to_the_same_set(box):
    assert write(box, os.path.join(box["out"], "n.ipynb"), tool="NotebookEdit")[0] == 0
    assert write(box, "/home/lumen/foreman/n.ipynb", tool="NotebookEdit")[0] == 2
    assert write(box, "/home/lumen/n.ipynb", tool="NotebookEdit")[0] == 2


def test_guard_hook_matchers_cover_every_file_tool():
    for rel in ("worker-settings.json", "permission-profiles/verify-probe.json"):
        with open(os.path.join(HARNESS, rel)) as f:
            pre = json.load(f)["hooks"]["PreToolUse"]
        m = [h["matcher"] for h in pre
             if any(x["command"].endswith("guard.sh") for x in h["hooks"])]
        assert m, rel
        for tool in ("Bash", "Write", "Edit", "NotebookEdit", "Read", "Grep", "Glob"):
            assert tool in m[0].split("|"), (rel, tool)


def test_issue_log_edit_exception_survives(box):
    assert write(box, "/home/lumen/kb/ISSUE-LOG.md", tool="Edit")[0] == 0
    assert write(box, "/home/lumen/kb/ISSUE-LOG.md", tool="Write")[0] == 2
    assert write(box, "/home/lumen/kb/../kb/ISSUE-LOG.md", tool="Edit")[0] == 2


def test_fire_plan_path_exception_survives(box):
    plan = "/home/lumen/foreman/plans/wq4830-fire-plan.md"
    assert write(box, plan, FOREMAN_FIRE_PLAN_PATH=plan)[0] == 0
    assert write(box, plan)[0] == 2


def test_too_broad_root_is_dropped_not_honoured(box):
    for broad in ("/home/lumen", "/", "/scratch", "/tmp"):
        assert write(box, "/home/lumen/wq4830_probe.txt", FOREMAN_OUTPUT_DIR=broad)[0] == 2
        assert write(box, "/home/lumen/wq4830_probe.txt",
                     FOREMAN_HERMETIC_EXTRA_WRITE=broad)[0] == 2


def test_extra_write_root_is_honoured_when_fworker_sets_one(box):
    assert write(box, os.path.join(box["elsewhere"], "a.txt"),
                 FOREMAN_HERMETIC_EXTRA_WRITE=box["elsewhere"])[0] == 0


def test_own_second_repo_lumen_pr_worktree_is_writable(box, tmp_path):
    """A worker changes a second repo in a lumen-pr worktree. It is writable when its
    name carries this ticket's id (the output dir's basename), and only then."""
    root = tmp_path / "pr-worktrees"
    out = tmp_path / "agent-work" / "4830"
    for d in ("bin-fix-wq4830-thing", "bin-fix-thing-wq4830", "bin-fix-wq48300-other",
              "bin-fix-wq5238-other", "foreman-docs-no-ticket"):
        (root / d).mkdir(parents=True)
    out.mkdir(parents=True)
    extra = {"LUMEN_PR_WORKTREE_ROOT": str(root), "FOREMAN_OUTPUT_DIR": str(out)}
    assert write(box, str(root / "bin-fix-wq4830-thing" / "wq"), **extra)[0] == 0
    assert write(box, str(root / "bin-fix-thing-wq4830" / "a" / "b.py"), **extra)[0] == 0
    for other in ("bin-fix-wq48300-other", "bin-fix-wq5238-other", "foreman-docs-no-ticket"):
        rc, err = write(box, str(root / other / "x.py"), **extra)
        assert rc == 2 and "branch name carries wq4830" in err, other
    assert write(box, str(root / "new-file-at-root.py"), **extra)[0] == 2
    assert bash(box, f"cp a.txt {root}/bin-fix-wq4830-thing/a.txt",
                FOREMAN_HERMETIC_BASH="enforce", **extra)[0] == 0
    assert bash(box, f"cp a.txt {root}/bin-fix-wq5238-other/a.txt",
                FOREMAN_HERMETIC_BASH="enforce", **extra)[0] == 2


# ---- modes -------------------------------------------------------------------------

def test_shadow_mode_logs_and_allows(box):
    rc, _ = write(box, os.path.join(box["elsewhere"], "a.txt"), FOREMAN_HERMETIC="shadow")
    assert rc == 0
    rows = [r for r in events(box) if r["event"] == "hermetic_shadow"]
    assert rows and "writable set" in rows[0]["reason"]


def test_shadow_mode_still_refuses_a_resolved_primary_tree_target(box):
    target = os.path.join(box["worktree"], *[".."] * 12, "home/lumen/foreman/harness/zz.py")
    assert write(box, target, FOREMAN_HERMETIC="shadow")[0] == 2


def test_off_mode_skips_the_contract(box):
    assert write(box, os.path.join(box["elsewhere"], "a.txt"), FOREMAN_HERMETIC="off")[0] == 0
    assert write(box, os.path.join(box["elsewhere"], "a.txt"), FOREMAN_HERMETIC="OFF")[0] == 0


@pytest.mark.parametrize("typo", ["enfroce", "true", "1", "disabled"])
def test_unknown_mode_value_means_enforce(box, typo):
    assert write(box, os.path.join(box["elsewhere"], "a.txt"), FOREMAN_HERMETIC=typo)[0] == 2


def test_missing_helper_fails_closed(box, tmp_path):
    lone = tmp_path / "lone"
    lone.mkdir()
    shutil.copy(GUARD, lone / "guard.sh")
    rc, err = write(box, os.path.join(box["worktree"], "a.txt"), guard_path=str(lone / "guard.sh"))
    assert rc == 2 and "BLOCKED" in err


def test_helper_crash_fails_closed(box, tmp_path):
    lone = tmp_path / "crash"
    lone.mkdir()
    shutil.copy(GUARD, lone / "guard.sh")
    (lone / "hermetic.py").write_text("import sys\nsys.exit(1)\n")
    rc, err = write(box, os.path.join(box["worktree"], "a.txt"), guard_path=str(lone / "guard.sh"))
    assert rc == 2 and "fail-closed" in err


def test_settings_precedence_default_config_env(tmp_path, monkeypatch):
    cfg = tmp_path / "hermetic.json"
    monkeypatch.setattr(hermetic, "CONFIG_PATH", str(cfg))
    assert hermetic.settings({}) == {"FOREMAN_HERMETIC": "enforce",
                                     "FOREMAN_HERMETIC_BASH": "shadow",
                                     "FOREMAN_HERMETIC_MOUNT": "off"}
    cfg.write_text(json.dumps({"modes": {"FOREMAN_HERMETIC_BASH": "enforce",
                                         "FOREMAN_HERMETIC_MOUNT": "enforce"}}))
    s = hermetic.settings({})
    assert s["FOREMAN_HERMETIC_BASH"] == "enforce" and s["FOREMAN_HERMETIC_MOUNT"] == "enforce"
    assert hermetic.settings({"FOREMAN_HERMETIC_MOUNT": "off"})["FOREMAN_HERMETIC_MOUNT"] == "off"
    assert hermetic.settings({"FOREMAN_HERMETIC": "bogus"})["FOREMAN_HERMETIC"] == "enforce"
    cfg.write_text("{not json")
    with pytest.raises(hermetic.HermeticError):
        hermetic.settings({})


# ---- the output dir ----------------------------------------------------------------

def test_output_dir_is_per_ticket_under_the_root(tmp_path, monkeypatch):
    monkeypatch.setenv("FOREMAN_OUTPUT_ROOT", str(tmp_path / "agent-work"))
    d = hermetic.ensure_output_dir(4830)
    assert d == str(tmp_path / "agent-work" / "4830") and os.path.isdir(d)
    assert hermetic.ensure_output_dir(4830) == d      # idempotent
    monkeypatch.delenv("FOREMAN_OUTPUT_ROOT")
    assert hermetic.output_dir(4830) == "/scratch/agent-work/4830"


@pytest.mark.parametrize("bad", ["../x", "a/b", "", ".", "..", "4830/../1", "a b"])
def test_output_dir_refuses_a_ticket_id_that_is_a_path(bad):
    with pytest.raises(hermetic.HermeticError):
        hermetic.output_dir(bad)


def test_output_dir_refuses_a_planted_symlink(tmp_path, monkeypatch):
    root = tmp_path / "agent-work"
    root.mkdir()
    os.symlink("/home/lumen", root / "77")
    monkeypatch.setenv("FOREMAN_OUTPUT_ROOT", str(root))
    with pytest.raises(hermetic.HermeticError):
        hermetic.ensure_output_dir(77)


def test_build_env_hands_the_child_its_output_dir_and_explicit_modes(tmp_path, monkeypatch):
    import fworker
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setattr(fworker, "read_oauth_token", lambda: "tok")
    monkeypatch.setattr(hermetic, "CONFIG_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setenv("FOREMAN_HERMETIC_EXTRA_WRITE", "/home/lumen")
    monkeypatch.delenv("FOREMAN_HERMETIC", raising=False)
    monkeypatch.delenv("FOREMAN_HERMETIC_BASH", raising=False)
    monkeypatch.delenv("FOREMAN_HERMETIC_MOUNT", raising=False)
    env = fworker.build_env("w1", "sonnet-5", "high", str(repo), 483001)
    assert env["FOREMAN_OUTPUT_DIR"] == os.path.join(os.environ["FOREMAN_OUTPUT_ROOT"], "483001")
    assert os.path.isdir(env["FOREMAN_OUTPUT_DIR"])
    assert not env["FOREMAN_OUTPUT_DIR"].startswith("/scratch/agent-work")
    assert (env["FOREMAN_HERMETIC"], env["FOREMAN_HERMETIC_BASH"],
            env["FOREMAN_HERMETIC_MOUNT"]) == ("enforce", "shadow", "off")
    assert "FOREMAN_HERMETIC_EXTRA_WRITE" not in env
    assert fworker.hermetic_output_dirs(483001) == [env["FOREMAN_OUTPUT_DIR"]]


# ---- Bash: explicit write destinations ---------------------------------------------

@pytest.mark.parametrize("cmd", [
    "echo x >> /home/lumen/foreman/harness/zz",
    "echo x > /home/lumen/foreman/harness/zz",
    "cp a.txt /home/lumen/foreman/harness/zz",
    "mv a.txt /home/lumen/foreman/",
    "sed -i s/a/b/ /home/lumen/foreman/harness/guard.sh",
    "sed -i.bak -e s/a/b/ /home/lumen/foreman/harness/guard.sh",
    "printf x | tee /home/lumen/foreman/harness/zz",
    "touch /home/lumen/foreman/STOP",
    "true && cp -t /home/lumen/foreman/harness a.txt",
    "cd /home/lumen/foreman && echo x > harness/zz",
    "cd /home/lumen && cp a.txt foreman/harness/zz",
    "echo x > ~/foreman/harness/zz",
    "echo x > $HOME/foreman/harness/zz",
    "echo x > /home/lumen/foreman/harness/$name",
    "cp a.txt /home/lumen/foreman/harness/guard.sh.bak.$(date +%s)",
    "ln -s /tmp/x /home/lumen/foreman/harness/link",
    "rm /home/lumen/foreman/harness/guard.sh",
    "dd if=/dev/zero of=/home/lumen/foreman/harness/zz",
])
def test_bash_write_into_the_foreman_tree_is_refused(box, cmd):
    rc, err = bash(box, cmd)
    assert rc == 2, cmd
    assert "BLOCKED" in err


@pytest.mark.parametrize("cmd", [
    "cp /home/lumen/foreman/harness/README.md ./README.copy.md",     # copy FROM
    "cat /home/lumen/foreman/harness/README.md > notes.txt",
    'grep ">" /home/lumen/foreman/harness/README.md',                # quoted operator
    "grep -c x /home/lumen/foreman/harness/README.md 2>&1",
    "ls /home/lumen/foreman/harness >/dev/null 2>&1",
    "diff /home/lumen/foreman/harness/README.md README.copy.md",
    "cat > note.md <<'EOF'\ncp a.txt /home/lumen/foreman/harness/zz\nEOF",   # heredoc body
    "sed -n 1,5p /home/lumen/foreman/harness/README.md",             # sed without -i
    "python3 -m pytest tests/test_wq4830_hermetic.py -q",
])
def test_bash_reading_the_foreman_tree_is_not_refused(box, cmd):
    rc, err = bash(box, cmd)
    assert rc == 0, (cmd, err)


@pytest.mark.parametrize("cmd", [
    "cat ~/.secrets/foo",
    "cat $HOME/.secrets/foo",
    "cat ${HOME}/.secrets/foo",
    "cd /home/lumen && cat .secrets/foo",
    "cd ~ ; ls .secrets",
    "cat /home/lumen/.sec*/foo",
    "ls /home/lumen/.s*",
    "cat /home/lumen/foreman/../.secrets/foo",
    "cp /home/lumen/.secrets/foo out.txt",
    "tar -cf x.tar --directory=/home/lumen/.secrets .",
    "echo x > /home/lumen/.secrets/new.token",
])
def test_bash_cannot_spell_its_way_into_the_secrets_dir(box, cmd):
    rc, err = bash(box, cmd)
    assert rc == 2, cmd


@pytest.mark.parametrize("cmd", [
    'grep -rn "\\.secrets" .',            # searching code for the string
    "mkdir -p .secrets && touch .secrets/fixture",   # a dir of that name in the worktree
    "ls /home/lumen/*.md",                # a glob that cannot match a dotdir
])
def test_secrets_check_is_by_resolution_not_by_substring(box, cmd):
    rc, err = bash(box, cmd)
    assert rc == 0, (cmd, err)


SOFT = ["echo x > /home/lumen/wq4830_probe", "touch /home/lumen/.bashrc",
        "cp a.txt /tmp/wq4830_probe", "mkdir -p /home/lumen/newdir"]


@pytest.mark.parametrize("cmd", SOFT)
def test_bash_write_elsewhere_is_logged_in_shadow_and_refused_in_enforce(box, cmd):
    assert bash(box, cmd)[0] == 0                                    # default: shadow
    rows = [r for r in events(box) if r["event"] == "hermetic_shadow_bash"]
    assert rows and "outside this ticket's writable set" in rows[0]["reason"]
    rc, err = bash(box, cmd, FOREMAN_HERMETIC_BASH="enforce")
    assert rc == 2 and "hermetic output contract (wq#4830)" in err
    assert bash(box, cmd, FOREMAN_HERMETIC_BASH="off")[0] == 0


def test_bash_writes_inside_the_set_pass_in_enforce(box):
    for cmd in ("echo x > $FOREMAN_OUTPUT_DIR/report.md",
                f"cp a.txt {box['out']}/a.txt",
                f"mkdir -p {box['scratch']}/build && touch {box['scratch']}/build/x",
                "echo x > notes.txt",
                "mkdir -p reports && cp a.txt reports/",
                "echo x >&2", "echo x 2>/dev/null >/dev/null"):
        rc, err = bash(box, cmd, FOREMAN_HERMETIC_BASH="enforce",
                       TMPDIR=os.path.join(box["worktree"], ".tmp"))
        assert rc == 0, (cmd, err)
    assert not [r for r in events(box) if r["event"].startswith("hermetic_shadow")]


def test_check_bash_units(box):
    env = _env(box)
    kinds = lambda cmd, cwd=box["worktree"]: [k for k, _, _ in hermetic.check_bash(cmd, env, cwd)]
    assert kinds("echo done >> /home/lumen/kb/ISSUE-LOG.md") == []       # wq#1831 append
    assert kinds("echo x > /home/lumen/kb/ISSUE-LOG.md") == ["soft"]     # overwrite is not
    assert kinds("echo x > /home/lumen/foreman/_runs/wq1/note") == ["soft"]  # runtime, not source
    assert kinds("echo 'unterminated > /home/lumen/foreman/x") == []     # unparseable: no verdict
    assert kinds("echo x > rel.txt", cwd=None) == []                     # no cwd, no guess
    assert kinds("rsync -a out/ thoth:/srv/x/") == []                    # remote dest
    assert kinds("chmod 755 /home/lumen/foreman/harness/guard.sh") == ["hard"]
    assert kinds("FOO=1 nohup cp a /home/lumen/foreman/b") == ["hard"]
    # ln / rm / mv act on the name: a link that POINTS into the foreman tree is not a
    # write into it (wq4908's `ln -s <foreman file> /home/lumen/bin/<name>` replayed).
    link = os.path.join(box["elsewhere"], "launch")
    os.symlink("/home/lumen/foreman/harness/README.md", link)
    assert kinds(f"ln -sf /home/lumen/foreman/harness/README.md {link}") == ["soft"]
    assert kinds(f"rm {link}") == ["soft"]
    assert kinds(f"echo x > {link}") == ["hard"]                         # a redirect follows it
    assert kinds(f"sed -i s/a/b/ {link}") == ["hard"]
    assert hermetic.tokenize('a ">" b>c 2>&1') == [("a", False), (">", False), ("b", False),
                                                    (">", True), ("c", False), ("2", False),
                                                    (">&", True), ("1", False)]


# ---- the mount layer: real bubblewrap ----------------------------------------------

def _bwrap_works():
    b = shutil.which("bwrap")
    if not b:
        return False
    p = subprocess.run([b, "--ro-bind", "/", "/", "true"], capture_output=True)
    return p.returncode == 0


needs_bwrap = pytest.mark.skipif(not _bwrap_works(),
                                 reason="bwrap / unprivileged user namespaces unavailable")


@pytest.fixture
def repo_box(monkeypatch):
    """A box outside /tmp (the sandbox gives the child a private /tmp, so a fixture
    living there would be invisible to it, not read-only), with the worktree a real
    linked git worktree and a secrets dir to hide."""
    import pathlib
    import tempfile
    tmp_path = pathlib.Path(tempfile.mkdtemp(prefix="wq4830-test-", dir="/var/tmp"))
    box = {k: str(tmp_path / k) for k in ("worktree", "out", "scratch", "elsewhere", "events")}
    for p in box.values():
        os.mkdir(p)
    try:
        yield _repo_box(box, tmp_path, monkeypatch)
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def _repo_box(box, tmp_path, monkeypatch):
    primary = tmp_path / "primary"
    primary.mkdir()
    g = lambda *a, cwd=primary: subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *a],
        cwd=str(cwd), check=True, capture_output=True, text=True)
    g("init", "-q", "-b", "main")
    (primary / "live.py").write_text("live = 1\n")
    g("add", "-A")
    g("commit", "-q", "-m", "init")
    os.rmdir(box["worktree"])
    g("worktree", "add", "-q", "-b", "fix/wq4830", box["worktree"])
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "probe.token").write_text("s3cret\n")
    monkeypatch.setattr(hermetic, "SECRETS_DIR", str(secrets))
    monkeypatch.setattr(hermetic, "FOREMAN_ROOT", str(primary))
    monkeypatch.setattr(hermetic, "CONFIG_PATH", str(tmp_path / "absent.json"))
    box.update(primary=str(primary), secrets=str(secrets))
    return box


def sandboxed(box, script, **extra):
    env = _env(box, FOREMAN_HERMETIC_MOUNT="enforce", **extra)
    argv = hermetic.wrap(["sh", "-c", script], box["worktree"], env)
    return subprocess.run(argv, capture_output=True, text=True, env=env, timeout=60)


def test_mount_layer_is_off_by_default(box):
    cmd = ["claude", "-p", "x"]
    assert hermetic.wrap(cmd, box["worktree"], _env(box)) == cmd
    assert hermetic.wrap(cmd, box["worktree"], _env(box, FOREMAN_HERMETIC_MOUNT="off")) == cmd


@needs_bwrap
def test_mount_writes_inside_the_set_succeed(repo_box):
    b = repo_box
    p = sandboxed(b, f"echo a > {b['worktree']}/a.txt && echo b > {b['out']}/b.txt && "
                     f"echo c > {b['scratch']}/c.txt && pwd")
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == os.path.realpath(b["worktree"])
    for root, name in (("worktree", "a.txt"), ("out", "b.txt"), ("scratch", "c.txt")):
        assert os.path.exists(os.path.join(b[root], name))       # visible on the host


@needs_bwrap
@pytest.mark.parametrize("target", [
    "{primary}/live.py",                       # the primary's working files
    "{primary}/new.py",
    "{elsewhere}/x.txt",                       # a dir that is nobody's
    "/home/lumen/wq4830_probe.txt",
    "/home/lumen/.bashrc",
    "/home/lumen/foreman/harness/guard.sh",    # the live harness itself
    "/home/lumen/foreman/wq4830_probe",
    "/home/lumen/bin/wq4830_probe",
    "/scratch/agent-work/wq4830_probe",        # the output root, outside its own dir
    "/etc/wq4830_probe",
])
def test_mount_write_outside_the_set_is_refused_by_the_kernel(repo_box, target):
    target = target.format(**repo_box)
    before = os.path.exists(target) and open(target, "rb").read()
    # python, not the shell: the write goes through an interpreter the guard cannot read
    p = sandboxed(repo_box, f"python3 -c \"open('{target}', 'a').write('x')\"")
    assert p.returncode != 0
    assert "Read-only file system" in p.stderr or "Permission denied" in p.stderr
    assert (os.path.exists(target) and open(target, "rb").read()) == before


@needs_bwrap
def test_mount_makes_the_secrets_dir_invisible(repo_box):
    p = sandboxed(repo_box, f"ls -A {repo_box['secrets']} | wc -l; "
                            f"cat {repo_box['secrets']}/probe.token; echo rc=$?")
    assert p.stdout.split() == ["0", "rc=1"]
    assert "s3cret" not in p.stdout + p.stderr
    assert open(os.path.join(repo_box["secrets"], "probe.token")).read() == "s3cret\n"


@needs_bwrap
@pytest.mark.skipif(not os.path.isdir(SECRETS), reason="no live secrets dir on this host")
def test_mount_hides_the_live_secrets_dir(box, tmp_path, monkeypatch):
    monkeypatch.setattr(hermetic, "CONFIG_PATH", str(tmp_path / "absent.json"))
    p = sandboxed(box, f"ls -A {SECRETS} | wc -l")
    assert p.returncode == 0 and p.stdout.strip() == "0"


@needs_bwrap
def test_mount_keeps_the_pr_path_working(repo_box):
    """lumen-pr's commit is a git commit in the linked worktree: objects and refs land
    in the primary's .git, while the primary's working files stay read-only."""
    b = repo_box
    p = sandboxed(b, "echo change > change.txt && git add change.txt && "
                     "git -c user.name=t -c user.email=t@example.invalid commit -q -m wq4830 && "
                     "git log --oneline -1")
    assert p.returncode == 0, p.stderr
    assert "wq4830" in p.stdout
    log = subprocess.run(["git", "-C", b["primary"], "log", "--oneline", "-1", "fix/wq4830"],
                         capture_output=True, text=True)
    assert "wq4830" in log.stdout                      # the commit reached the real repo
    assert open(os.path.join(b["primary"], "live.py")).read() == "live = 1\n"


@needs_bwrap
def test_mount_tmp_is_private(repo_box, tmp_path):
    name = f"/tmp/wq4830-private-{os.getpid()}"
    p = sandboxed(repo_box, f"echo x > {name} && cat {name}")
    assert p.returncode == 0 and p.stdout.strip() == "x"
    assert not os.path.exists(name)


def test_mount_refuses_to_build_without_bwrap(box, monkeypatch):
    monkeypatch.setattr(hermetic.shutil, "which", lambda name: None)
    with pytest.raises(hermetic.HermeticError):
        hermetic.wrap(["true"], box["worktree"], _env(box, FOREMAN_HERMETIC_MOUNT="enforce"))


def test_mount_refuses_a_writable_path_inside_a_hidden_one(repo_box):
    inside = os.path.join(repo_box["secrets"], "sub")
    os.mkdir(inside)
    with pytest.raises(hermetic.HermeticError):
        hermetic.mount_prefix(repo_box["worktree"],
                              _env(repo_box, FOREMAN_HERMETIC_EXTRA_WRITE=inside))
