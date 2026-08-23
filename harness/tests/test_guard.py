"""guard.sh corpus test — BUILD_PLAN.md §T2a adversarial verify item 2.

Zero quota. Feeds the hook JSON envelope guard.sh actually receives on stdin and
asserts exit code. Every deny class gets a straight hit AND a quoting/bash-c/
whitespace evasion; every allow class gets a straight pass.
"""
import json
import os
import subprocess
import tempfile

GUARD = os.path.join(os.path.dirname(__file__), "..", "guard.sh")
WORKTREE = "/home/lumen/worktrees/wq999-a1"
# A real file, for Read must-allow / bounding tests now that guard.sh's wq#730 R6
# check requires the target to actually exist on disk.
EXISTING_FILE = os.path.join(os.path.dirname(__file__), "..", "README.md")


def run_guard(tool_name, tool_input, env_extra=None, session_id="test-session",
              cwd=WORKTREE):
    payload = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": session_id,
        "cwd": cwd,
        "effort": {"level": "high"},
    })
    env = dict(os.environ)
    env["FOREMAN_WORKER"] = "test-worker"
    # Fresh dir per call by default so the wq#730 R5 read-cache never leaks
    # state across tests or across pytest runs; tests that need to reuse the
    # cache across calls pass FOREMAN_EVENTS_DIR explicitly via env_extra.
    env["FOREMAN_EVENTS_DIR"] = tempfile.mkdtemp(prefix="guard_test_events_")
    env["FOREMAN_WORKTREE_ROOT"] = WORKTREE
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([GUARD], input=payload, capture_output=True, text=True, env=env)
    return p.returncode, p.stderr


def run_guard_stdout(tool_name, tool_input, env_extra=None, session_id="test-session"):
    payload = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": session_id,
        "cwd": WORKTREE,
        "effort": {"level": "high"},
    })
    env = dict(os.environ)
    env["FOREMAN_WORKER"] = "test-worker"
    # Fresh dir per call by default so the wq#730 R5 read-cache never leaks
    # state across tests or across pytest runs; tests that need to reuse the
    # cache across calls pass FOREMAN_EVENTS_DIR explicitly via env_extra.
    env["FOREMAN_EVENTS_DIR"] = tempfile.mkdtemp(prefix="guard_test_events_")
    env["FOREMAN_WORKTREE_ROOT"] = WORKTREE
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([GUARD], input=payload, capture_output=True, text=True, env=env)
    return p.returncode, p.stdout, p.stderr


MUST_BLOCK = [
    ("Bash", {"command": "sudo reboot"}),
    ("Bash", {"command": "reboot"}),
    ("Bash", {"command": "sudo -i"}),
    ("Bash", {"command": "shutdown -h now"}),
    ("Bash", {"command": "poweroff"}),
    ("Bash", {"command": "systemctl stop lumen-agent"}),
    ("Bash", {"command": "systemctl disable lumen-agent"}),
    ("Bash", {"command": "systemctl mask lumen-agent"}),
    ("Bash", {"command": "dd if=/dev/zero of=/dev/sda"}),
    ("Bash", {"command": "docker compose --remove-orphans up -d"}),
    ("Bash", {"command": "bash -c \"docker compose --remove-orphans up -d\""}),
    ("Bash", {"command": "docker    compose\t--remove-orphans"}),
    ("Bash", {"command": "ip link set enp0s25 address 00:11:22:33:44:55"}),
    ("Bash", {"command": "macchanger -r enp0s25"}),
    ("Bash", {"command": "git push --force origin main"}),
    ("Bash", {"command": "git push -f origin main"}),
    ("Bash", {"command": "git push --force-with-lease origin main"}),
    ("Bash", {"command": "rm -rf /"}),
    ("Bash", {"command": "rm -rf /home"}),
    ("Bash", {"command": "rm -fr /etc"}),
    ("Bash", {"command": "docker rm -f mycontainer"}),
    ("Bash", {"command": "docker volume prune"}),
    ("Bash", {"command": "mkfs.ext4 /dev/sda1"}),
    ("Bash", {"command": "wipefs -a /dev/sda"}),
    ("Bash", {"command": "claude setup-token"}),
    ("Bash", {"command": "ANTHROPIC_API_KEY=sk-foo claude -p hi"}),
    ("Bash", {"command": "export ANTHROPIC_BASE_URL=http://evil"}),
    ("Bash", {"command": "init 0"}),
    ("Bash", {"command": "  sudo   reboot  "}),
    ("Read", {"file_path": "/home/lumen/agent/.env"}),
    ("Read", {"file_path": "/home/lumen/foreman/.credentials.json"}),
    ("Read", {"file_path": "/home/lumen/.claude/.credentials.json"}),
    ("Edit", {"file_path": "/home/lumen/foreman/claude_credentials.md"}),
    ("Write", {"file_path": "/home/lumen/foreman/harness/.env"}),
    # wq#616: the durable oauth token every cloud worker's own `claude -p` consumes via
    # CLAUDE_CODE_OAUTH_TOKEN env — must never be readable/exfiltratable by the worker's
    # own tools, Bash included (Read-tool deny alone doesn't stop `cat`/`grep`).
    ("Read", {"file_path": "/home/lumen/.secrets/claude-oat-worker.token"}),
    ("Bash", {"command": "cat /home/lumen/.secrets/claude-oat-worker.token"}),
    ("Bash", {"command": "grep oat /home/lumen/.secrets/claude-oat-worker.token"}),
    # wq#717: Grep/Glob use "path", not "file_path" — a Read-only deny list that
    # forgot them would let a fire-mode worker exfiltrate FIRE_TOKEN_FILE (the
    # bearer token guarding "MUST NEVER approve itself") via a search hit instead
    # of a direct Read.
    ("Grep", {"pattern": ".", "path": "/home/lumen/.secrets/foreman-fire-approval.token"}),
    ("Glob", {"pattern": "*", "path": "/home/lumen/.secrets"}),
]

MUST_ALLOW = [
    ("Bash", {"command": "git push origin feat/wq625"}),
    ("Bash", {"command": "docker ps -a"}),
    ("Bash", {"command": "docker logs mycontainer"}),
    ("Bash", {"command": f"rm -rf {WORKTREE}/build"}),
    ("Bash", {"command": "rm -rf /tmp/scratch-wq625"}),
    ("Bash", {"command": "pytest -x tests/"}),
    ("Bash", {"command": "python3 -m pytest -v"}),
    ("Bash", {"command": "sqlite3 queue.db 'select 1'"}),
    ("Bash", {"command": "curl -s http://localhost:8080/x"}),
    ("Bash", {"command": "git log --oneline -5"}),
    ("Read", {"file_path": EXISTING_FILE, "limit": 50}),
    ("Write", {"file_path": f"{WORKTREE}/src/main.py"}),
    ("Grep", {"pattern": "TODO", "path": WORKTREE}),
    ("Glob", {"pattern": "**/*.py", "path": WORKTREE}),
]


def test_must_block():
    failures = []
    for tool, inp in MUST_BLOCK:
        rc, stderr = run_guard(tool, inp)
        if rc != 2:
            failures.append((tool, inp, rc, stderr))
    assert not failures, f"expected exit 2 (block), got: {failures}"


def test_must_allow():
    failures = []
    for tool, inp in MUST_ALLOW:
        rc, stderr = run_guard(tool, inp)
        if rc != 0:
            failures.append((tool, inp, rc, stderr))
    assert not failures, f"expected exit 0 (allow), got: {failures}"


def test_malformed_stdin_fails_closed():
    env = dict(os.environ)
    env["FOREMAN_WORKER"] = "test-worker"
    p = subprocess.run([GUARD], input="not json{{{", capture_output=True, text=True, env=env)
    assert p.returncode == 2


def test_empty_stdin_fails_closed():
    env = dict(os.environ)
    env["FOREMAN_WORKER"] = "test-worker"
    p = subprocess.run([GUARD], input="", capture_output=True, text=True, env=env)
    assert p.returncode == 2


def test_missing_tool_name_fails_closed():
    env = dict(os.environ)
    env["FOREMAN_WORKER"] = "test-worker"
    p = subprocess.run([GUARD], input=json.dumps({"cwd": "/tmp"}), capture_output=True, text=True, env=env)
    assert p.returncode == 2


def test_off_host_fails_closed(monkeypatch):
    # guard.sh checks `hostname` directly; simulate off-host by pointing PATH at a
    # shim that reports a different name.
    shim_dir = "/tmp/guard_test_hostname_shim"
    os.makedirs(shim_dir, exist_ok=True)
    shim = os.path.join(shim_dir, "hostname")
    with open(shim, "w") as f:
        f.write("#!/bin/sh\necho notlumen\n")
    os.chmod(shim, 0o755)
    env = dict(os.environ)
    env["PATH"] = shim_dir + ":" + env.get("PATH", "")
    env["FOREMAN_WORKER"] = "test-worker"
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}})
    p = subprocess.run([GUARD], input=payload, capture_output=True, text=True, env=env)
    assert p.returncode == 2


def test_lifecycle_events_logged(tmp_path):
    env = dict(os.environ)
    env["FOREMAN_WORKER"] = "test-worker-lifecycle"
    env["FOREMAN_EVENTS_DIR"] = str(tmp_path)
    payload = json.dumps({"session_id": "s-abc", "cwd": "/tmp", "effort": {"level": "high"}})
    for phase in ("start", "stop", "end"):
        p = subprocess.run([GUARD, "--lifecycle", phase], input=payload, capture_output=True, text=True, env=env)
        assert p.returncode == 0, p.stderr
    events_file = tmp_path / "test-worker-lifecycle.jsonl"
    assert events_file.exists()
    lines = [json.loads(l) for l in events_file.read_text().splitlines() if l.strip()]
    assert [l["event"] for l in lines] == ["start", "stop", "end"]
    assert all(l["effort"] == "high" for l in lines)
    assert all(l["session_id"] == "s-abc" for l in lines)


# ---- wq#729 R4: Bash output cap ----

CAPPABLE = [
    "cat /etc/hostname",
    "find / -maxdepth 4 -iname wq*",
    "git log --oneline -5",
    "grep -rn foo .",
]

# Bare & inside a quoted string (not a real background operator) is over-cautious
# to skip, but skipping is the safe direction — asserted separately below.
CAPPABLE_BUT_CONSERVATIVELY_SKIPPED = [
    'curl -s "http://localhost:8080/x?a=1&b=2"',
]

NOT_CAPPABLE = [
    "cd /tmp",
    "cd /tmp && pytest",
    "export FOO=bar",
    "source venv/bin/activate",
    "some-long-job.sh &",
    "sleep 5 & wait",
]


def test_capped_commands_get_updated_input():
    for cmd in CAPPABLE:
        rc, stdout, stderr = run_guard_stdout("Bash", {"command": cmd})
        assert rc == 0, (cmd, stderr)
        out = json.loads(stdout)
        updated = out["hookSpecificOutput"]["updatedInput"]["command"]
        assert "head -c 2000" in updated
        assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
        # the original command text is preserved verbatim inside the wrapper
        assert cmd in updated


def test_stateful_commands_are_not_capped():
    for cmd in NOT_CAPPABLE + CAPPABLE_BUT_CONSERVATIVELY_SKIPPED:
        rc, stdout, stderr = run_guard_stdout("Bash", {"command": cmd})
        assert rc == 0, (cmd, stderr)
        assert stdout.strip() == "", (cmd, stdout)


def test_cap_bytes_env_override():
    rc, stdout, stderr = run_guard_stdout(
        "Bash", {"command": "cat /etc/hostname"},
        env_extra={"FOREMAN_BASH_OUTPUT_CAP_BYTES": "500"},
    )
    assert rc == 0, stderr
    out = json.loads(stdout)
    assert "head -c 500" in out["hookSpecificOutput"]["updatedInput"]["command"]


def test_wrapped_command_actually_truncates(tmp_path):
    # Functional check: what guard.sh hands back, when actually run through bash,
    # produces output capped at the requested byte count.
    big = tmp_path / "big.txt"
    big.write_text("x" * 10_000)
    rc, stdout, stderr = run_guard_stdout("Bash", {"command": f"cat {big}"})
    assert rc == 0, stderr
    wrapped = json.loads(stdout)["hookSpecificOutput"]["updatedInput"]["command"]
    p = subprocess.run(["bash", "-c", wrapped], capture_output=True, text=True)
    assert len(p.stdout) == 2000


def test_wrapped_command_preserves_normal_output():
    rc, stdout, stderr = run_guard_stdout("Bash", {"command": "echo hello-wq729"})
    assert rc == 0, stderr
    wrapped = json.loads(stdout)["hookSpecificOutput"]["updatedInput"]["command"]
    p = subprocess.run(["bash", "-c", wrapped], capture_output=True, text=True)
    assert p.stdout.strip() == "hello-wq729"


def test_must_allow_commands_still_exit_zero_when_capped():
    # cross-check against the existing MUST_ALLOW corpus: capping must never
    # change the allow/block verdict, only (optionally) rewrite the command.
    for tool, inp in MUST_ALLOW:
        rc, stdout, stderr = run_guard_stdout(tool, inp)
        assert rc == 0, (tool, inp, stderr)


# ---- wq#730 R3: bound Read ----

def test_read_without_limit_gets_a_default_limit():
    rc, stdout, stderr = run_guard_stdout("Read", {"file_path": EXISTING_FILE})
    assert rc == 0, stderr
    out = json.loads(stdout)
    updated = out["hookSpecificOutput"]["updatedInput"]
    assert updated["file_path"] == EXISTING_FILE
    assert updated["limit"] == 300
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_read_with_explicit_limit_is_untouched():
    rc, stdout, stderr = run_guard_stdout("Read", {"file_path": EXISTING_FILE, "limit": 50})
    assert rc == 0, stderr
    assert stdout.strip() == ""


def test_read_offset_without_limit_still_gets_capped():
    rc, stdout, stderr = run_guard_stdout("Read", {"file_path": EXISTING_FILE, "offset": 10})
    assert rc == 0, stderr
    updated = json.loads(stdout)["hookSpecificOutput"]["updatedInput"]
    assert updated["offset"] == 10
    assert updated["limit"] == 300


def test_read_line_cap_env_override():
    rc, stdout, stderr = run_guard_stdout(
        "Read", {"file_path": EXISTING_FILE},
        env_extra={"FOREMAN_READ_LINE_CAP": "50"},
    )
    assert rc == 0, stderr
    updated = json.loads(stdout)["hookSpecificOutput"]["updatedInput"]
    assert updated["limit"] == 50


# ---- wq#730 R6: reject reads that can't possibly succeed ----

def test_read_nonexistent_file_is_blocked(tmp_path):
    missing = tmp_path / "does-not-exist.txt"
    rc, stderr = run_guard("Read", {"file_path": str(missing)})
    assert rc == 2
    assert "not a regular file" in stderr


def test_read_directory_is_blocked(tmp_path):
    rc, stderr = run_guard("Read", {"file_path": str(tmp_path)})
    assert rc == 2
    assert "not a regular file" in stderr


# ---- wq#730 R5: suppress repeat reads of an unchanged file ----

def test_repeat_read_same_offset_limit_is_suppressed(tmp_path):
    f = tmp_path / "same.txt"
    f.write_text("hello\n" * 5)
    events = {"FOREMAN_EVENTS_DIR": str(tmp_path / "events")}

    rc1, stdout1, stderr1 = run_guard_stdout("Read", {"file_path": str(f), "limit": 10}, env_extra=events)
    assert rc1 == 0, stderr1

    rc2, stderr2 = run_guard("Read", {"file_path": str(f), "limit": 10}, env_extra=events)
    assert rc2 == 2
    assert "already read this session" in stderr2


def test_repeat_read_after_file_changes_is_allowed(tmp_path):
    f = tmp_path / "changes.txt"
    f.write_text("v1\n")
    events = {"FOREMAN_EVENTS_DIR": str(tmp_path / "events")}

    rc1, stderr1 = run_guard("Read", {"file_path": str(f), "limit": 10}, env_extra=events)
    assert rc1 == 0, stderr1

    # An out-of-band mtime change (restore, rsync, touch) invalidates too.
    new_mtime = f.stat().st_mtime + 2
    f.write_text("v2\n")
    os.utime(f, (new_mtime, new_mtime))

    rc2, stderr2 = run_guard("Read", {"file_path": str(f), "limit": 10}, env_extra=events)
    assert rc2 == 0, stderr2


def test_repeat_read_after_same_second_change_is_allowed(tmp_path):
    """wq#833: Edit-then-Read inside one wall-clock second must not be misreported
    as unchanged. No os.utime fudge — the write really does land in the same
    integer second as the first read, which is what a fast agent loop does."""
    events = {"FOREMAN_EVENTS_DIR": str(tmp_path / "events")}

    for attempt in range(10):
        f = tmp_path / f"same-second-{attempt}.txt"
        f.write_text("v1\n")
        session = f"same-second-{attempt}"

        rc1, stderr1 = run_guard("Read", {"file_path": str(f), "limit": 10},
                                 env_extra=events, session_id=session)
        assert rc1 == 0, stderr1

        second_before = int(f.stat().st_mtime)
        f.write_text("v2\n")
        if int(f.stat().st_mtime) != second_before:
            continue  # the two writes straddled a second boundary; not the case under test

        rc2, stderr2 = run_guard("Read", {"file_path": str(f), "limit": 10},
                                 env_extra=events, session_id=session)
        assert rc2 == 0, f"same-second edit misreported as unchanged: {stderr2}"
        return

    raise AssertionError("every attempt straddled a second boundary — test never ran")


def test_repeat_read_different_offset_is_not_suppressed(tmp_path):
    f = tmp_path / "ranges.txt"
    f.write_text("hello\n" * 20)
    events = {"FOREMAN_EVENTS_DIR": str(tmp_path / "events")}

    rc1, stderr1 = run_guard("Read", {"file_path": str(f), "limit": 5}, env_extra=events)
    assert rc1 == 0, stderr1

    rc2, stderr2 = run_guard("Read", {"file_path": str(f), "offset": 5, "limit": 5}, env_extra=events)
    assert rc2 == 0, stderr2


def test_repeat_read_different_session_is_not_suppressed(tmp_path):
    f = tmp_path / "cross-session.txt"
    f.write_text("hello\n")
    events = {"FOREMAN_EVENTS_DIR": str(tmp_path / "events")}

    rc1, stderr1 = run_guard("Read", {"file_path": str(f), "limit": 5}, env_extra=events, session_id="session-a")
    assert rc1 == 0, stderr1

    rc2, stderr2 = run_guard("Read", {"file_path": str(f), "limit": 5}, env_extra=events, session_id="session-b")
    assert rc2 == 0, stderr2


# ---- wq#717 fire-mode diagnosis phase ------------------------------------------------

FIRE_DIAGNOSIS_ENV = {"FOREMAN_FIRE_PHASE": "diagnosis",
                      "FOREMAN_FIRE_PLAN_PATH": "/home/lumen/foreman/plans/wq717.md"}


def test_diagnosis_write_to_plan_path_allowed():
    rc, stderr = run_guard("Write", {"file_path": "/home/lumen/foreman/plans/wq717.md"},
                            env_extra=FIRE_DIAGNOSIS_ENV)
    assert rc == 0, stderr


def test_diagnosis_write_elsewhere_denied():
    rc, stderr = run_guard("Write", {"file_path": f"{WORKTREE}/notes.md"},
                            env_extra=FIRE_DIAGNOSIS_ENV)
    assert rc == 2


def test_diagnosis_edit_elsewhere_denied():
    rc, stderr = run_guard("Edit", {"file_path": EXISTING_FILE},
                            env_extra=FIRE_DIAGNOSIS_ENV)
    assert rc == 2


def test_diagnosis_write_without_plan_path_env_denied():
    rc, stderr = run_guard("Write", {"file_path": "/home/lumen/foreman/plans/wq717.md"},
                            env_extra={"FOREMAN_FIRE_PHASE": "diagnosis"})
    assert rc == 2


def test_write_outside_diagnosis_phase_unaffected():
    # No FOREMAN_FIRE_PHASE set (normal/apply-phase run) — Write anywhere in the
    # worktree is unaffected by the diagnosis-only restriction.
    rc, stderr = run_guard("Write", {"file_path": f"{WORKTREE}/notes.md"})
    assert rc == 0, stderr


DIAGNOSIS_MUST_BLOCK = [
    ("Bash", {"command": "systemctl restart lumen-agent"}),
    ("Bash", {"command": "systemctl start lumen-agent"}),
    ("Bash", {"command": "systemctl enable lumen-agent"}),
    ("Bash", {"command": "kill -9 12345"}),
    ("Bash", {"command": "pkill -f lumen"}),
    ("Bash", {"command": "service nginx restart"}),
    ("Bash", {"command": "apt-get install -y curl"}),
    ("Bash", {"command": "pip install requests"}),
    ("Bash", {"command": "mv /etc/foo /etc/foo.bak"}),
    ("Bash", {"command": "chmod 777 /etc/foo"}),
    ("Bash", {"command": "touch /tmp/marker"}),
    ("Bash", {"command": "sed -i 's/a/b/' /etc/foo.conf"}),
    ("Bash", {"command": "curl -X POST http://localhost/api -d '{}'"}),
    ("Bash", {"command": "curl --data foo=bar http://localhost/api"}),
]

DIAGNOSIS_MUST_ALLOW = [
    ("Bash", {"command": "systemctl status lumen-agent"}),
    ("Bash", {"command": "systemctl is-active lumen-agent"}),
    ("Bash", {"command": "journalctl -u lumen-agent -n 50"}),
    ("Bash", {"command": "ps aux | grep lumen"}),
    ("Bash", {"command": "curl -s http://localhost:8080/health"}),
    ("Bash", {"command": "cat /etc/foo.conf"}),
    ("Bash", {"command": "df -h"}),
]


def test_diagnosis_phase_blocks_mutating_bash():
    failures = []
    for tool, inp in DIAGNOSIS_MUST_BLOCK:
        rc, stderr = run_guard(tool, inp, env_extra=FIRE_DIAGNOSIS_ENV)
        if rc != 2:
            failures.append((tool, inp, rc, stderr))
    assert not failures, f"expected exit 2 (block), got: {failures}"


def test_diagnosis_phase_allows_readonly_bash():
    failures = []
    for tool, inp in DIAGNOSIS_MUST_ALLOW:
        rc, stderr = run_guard(tool, inp, env_extra=FIRE_DIAGNOSIS_ENV)
        if rc != 0:
            failures.append((tool, inp, rc, stderr))
    assert not failures, f"expected exit 0 (allow), got: {failures}"


def test_diagnosis_denylist_is_a_noop_outside_fire_phase():
    # Same mutating commands, no FOREMAN_FIRE_PHASE set — a normal/apply-phase
    # worker is governed by the house deny-list only, not the diagnosis denylist.
    rc, stderr = run_guard("Bash", {"command": "systemctl restart lumen-agent"})
    assert rc == 0, stderr


# ---- wq#900: PRIMARY trees are branch-frozen for workers ----
# The audit case verbatim: a worker's raw `git checkout` in /home/lumen/foreman
# switched the live dispatcher onto a feature branch. Deny any HEAD/branch-moving
# git verb when cwd is inside a registered primary tree or the command explicitly
# targets one; a worker's git work in its OWN worktree stays untouched.

PRIMARY = "/home/lumen/foreman"

PRIMARY_MUST_BLOCK = [
    # cwd inside a primary tree
    ({"command": "git checkout research/wq633-849-headroom-judge"}, PRIMARY),
    ({"command": "git switch feat/lumen-guardian"}, "/home/lumen/bin"),
    ({"command": "git checkout -b feat/new"}, PRIMARY),
    ({"command": "git reset --hard HEAD~1"}, f"{PRIMARY}/harness"),  # subdir counts
    ({"command": "git  \tcheckout main"}, PRIMARY),  # whitespace evasion
    ({"command": "git pull origin research/foo"}, PRIMARY),
    # cwd elsewhere, command targets the primary explicitly
    ({"command": f"git -C {PRIMARY} checkout research/foo"}, WORKTREE),
    ({"command": f"cd {PRIMARY} && git checkout research/foo"}, WORKTREE),
    ({"command": f"cd {PRIMARY}/harness && git switch x"}, WORKTREE),
    ({"command": f"git --git-dir={PRIMARY}/.git checkout x"}, WORKTREE),
    ({"command": f"git --work-tree={PRIMARY} checkout x"}, WORKTREE),
    ({"command": "git -C /home/lumen/bin checkout feat/x"}, WORKTREE),
    # merge is denied unless it is exactly the sanctioned deploy fast-forward
    ({"command": "git merge feat/x"}, PRIMARY),
    ({"command": "git merge origin/main"}, PRIMARY),  # missing --ff-only
    ({"command": "git merge --ff-only origin/feat/x"}, PRIMARY),  # non-default target
    ({"command": f"git -C {PRIMARY} merge --no-ff origin/main"}, WORKTREE),
    ({"command": "git merge --ff-only origin/main && git checkout feat/x"}, PRIMARY),
]

PRIMARY_MUST_ALLOW = [
    # normal branch work in the worker's own worktree
    ({"command": "git checkout -b feat/wq900"}, WORKTREE),
    ({"command": "git switch main"}, WORKTREE),
    ({"command": "git rebase main"}, WORKTREE),
    # mentioning a primary path without a git verb aimed at it
    ({"command": f"ls {PRIMARY}/harness"}, WORKTREE),
    ({"command": f"git log --oneline -5 && grep -rn checkout {PRIMARY}/docs"}, WORKTREE),
    # full-path wq call composed with local git work must not trip the
    # /home/lumen/bin registry entry
    ({"command": "git checkout main && /home/lumen/bin/wq done 1 --notes x"}, WORKTREE),
    # read-only git in a primary is fine
    ({"command": "git status"}, PRIMARY),
    ({"command": "git log --oneline -3"}, PRIMARY),
    # the sanctioned deploy fast-forward (repo_reconcile BEHIND_ONLY advice)
    ({"command": "git merge --ff-only origin/main"}, PRIMARY),
    ({"command": f"git -C {PRIMARY} merge --ff-only origin/main"}, WORKTREE),
    ({"command": "git -C /home/lumen/pstack merge --ff-only origin/master"}, WORKTREE),
]


def test_primary_tree_branch_switch_blocked():
    failures = []
    for inp, cwd in PRIMARY_MUST_BLOCK:
        rc, stderr = run_guard("Bash", inp, cwd=cwd)
        # "syntax error": a shell-metacharacter (backtick/quote) slip in the reason
        # string executes inside block()'s double quotes — caught live once; keep
        # the reason inert, not just the exit code right.
        if rc != 2 or "wq#900" not in stderr or "syntax error" in stderr:
            failures.append((inp, cwd, rc, stderr))
    assert not failures, f"expected clean wq#900 block, got: {failures}"


def test_primary_tree_allow_cases():
    failures = []
    for inp, cwd in PRIMARY_MUST_ALLOW:
        rc, stderr = run_guard("Bash", inp, cwd=cwd)
        if rc != 0:
            failures.append((inp, cwd, rc, stderr))
    assert not failures, f"expected exit 0 (allow), got: {failures}"


def test_primary_tree_env_override_respected():
    # A tree listed only via FOREMAN_PRIMARY_TREES is guarded too (fworker computes
    # the list from router.LANE_REPO at dispatch time).
    rc, stderr = run_guard(
        "Bash", {"command": "git checkout feature"},
        env_extra={"FOREMAN_PRIMARY_TREES": "/srv/custom-primary"},
        cwd="/srv/custom-primary")
    assert rc == 2 and "wq#900" in stderr, stderr
    # ...and the env var replaces the fallback list rather than unioning — fworker
    # always passes the full registry.
    rc, _ = run_guard(
        "Bash", {"command": "git checkout feature"},
        env_extra={"FOREMAN_PRIMARY_TREES": "/srv/custom-primary"},
        cwd=PRIMARY)
    assert rc == 0


def test_primary_tree_blocked_event_logged(tmp_path):
    events = tmp_path / "events"
    rc, _ = run_guard("Bash", {"command": "git checkout research/foo"},
                      env_extra={"FOREMAN_EVENTS_DIR": str(events)}, cwd=PRIMARY)
    assert rc == 2
    logged = (events / "test-worker.jsonl").read_text()
    assert "wq#900" in logged
