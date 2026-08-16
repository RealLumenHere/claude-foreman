"""guard.sh corpus test — BUILD_PLAN.md §T2a adversarial verify item 2.

Zero quota. Feeds the hook JSON envelope guard.sh actually receives on stdin and
asserts exit code. Every deny class gets a straight hit AND a quoting/bash-c/
whitespace evasion; every allow class gets a straight pass.
"""
import json
import os
import subprocess

GUARD = os.path.join(os.path.dirname(__file__), "..", "guard.sh")
WORKTREE = "/home/lumen/worktrees/wq999-a1"


def run_guard(tool_name, tool_input, env_extra=None):
    payload = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": "test-session",
        "cwd": WORKTREE,
        "effort": {"level": "high"},
    })
    env = dict(os.environ)
    env["FOREMAN_WORKER"] = "test-worker"
    env["FOREMAN_EVENTS_DIR"] = "/tmp/guard_test_events"
    env["FOREMAN_WORKTREE_ROOT"] = WORKTREE
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([GUARD], input=payload, capture_output=True, text=True, env=env)
    return p.returncode, p.stderr


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
    ("Read", {"file_path": f"{WORKTREE}/README.md"}),
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
