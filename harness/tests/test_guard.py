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


def run_guard(tool_name, tool_input, env_extra=None, session_id="test-session"):
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

    # Force a distinct mtime (some filesystems have 1s resolution).
    new_mtime = f.stat().st_mtime + 2
    f.write_text("v2\n")
    os.utime(f, (new_mtime, new_mtime))

    rc2, stderr2 = run_guard("Read", {"file_path": str(f), "limit": 10}, env_extra=events)
    assert rc2 == 0, stderr2


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
