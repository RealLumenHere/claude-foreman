"""guard.sh corpus test — BUILD_PLAN.md §T2a adversarial verify item 2.

Zero quota. Feeds the hook JSON envelope guard.sh actually receives on stdin and
asserts exit code. Every deny class gets a straight hit AND a quoting/bash-c/
whitespace evasion; every allow class gets a straight pass.
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import primary_tree_guard as ptg  # noqa: E402  # wq#1752 runtime-env simulation

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
    # wq#1548: the credential re-injection deny kept the pre-wq#879 leading-context
    # class, so the same quoted-verb evasion worked against it verbatim.
    ("Bash", {"command": "ssh bes 'ANTHROPIC_API_KEY=sk-x claude -p hi'"}),
    ("Bash", {"command": "bash -c \"ANTHROPIC_BASE_URL=http://evil claude -p hi\""}),
    ("Bash", {"command": "bash -c '(ANTHROPIC_API_KEY=sk-x claude -p hi)'"}),
    ("Bash", {"command": "init 0"}),
    ("Bash", {"command": "  sudo   reboot  "}),
    # wq#879: the denied-verb scan only looked for [;&| ] before the verb, so a verb
    # sitting immediately after an opening quote/paren was invisible. Disclosed by
    # wq#415, exercised again by wq#857's verify pass, closed at guard.sh:84.
    ("Bash", {"command": "ssh bes 'sudo ufw status'"}),
    ("Bash", {"command": "ssh bes \"sudo -n true\""}),
    ("Bash", {"command": "ssh bes 'sudo bash -c \"whoami\"'"}),
    ("Bash", {"command": "ssh bes 'poweroff'"}),
    ("Bash", {"command": "bash -c '(reboot)'"}),
    ("Read", {"file_path": "/home/lumen/agent/.env"}),
    ("Read", {"file_path": "/home/lumen/foreman/.credentials.json"}),
    ("Read", {"file_path": "/home/lumen/.claude/.credentials.json"}),
    ("Edit", {"file_path": "/home/lumen/foreman/claude_credentials.md"}),
    # wq#1454 secrets sweep of needs:dir grants: the .bak copies and the minerva context
    # mirror of the credentials section escaped the *claude_credentials.md glob.
    ("Read", {"file_path": "/home/lumen/minerva/context/claude_credentials.md.bak.20260828_171707"}),
    ("Read", {"file_path": "/home/lumen/minerva/context/sections/027_4-credentials-secrets.md"}),
    ("Grep", {"pattern": "token", "path": "/home/lumen/minerva/context/sections.json"}),
    ("Bash", {"command": "cat /home/lumen/minerva/context/claude_credentials.md.bak.20260828_171707"}),
    ("Bash", {"command": "grep -n key /home/lumen/minerva/context/sections/027_4-credentials-secrets.md"}),
    ("Bash", {"command": "tail -c 999 /home/lumen/minerva/context/sections.json"}),
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
    # wq#1297: large pulls (ollama/podman/git-lfs) are manual off-peak only, never
    # from a cron/fworker context — this hook only ever fires for that context.
    ("Bash", {"command": "ollama pull llama3:70b"}),
    ("Bash", {"command": "bash -c \"ollama pull llama3:70b\""}),
    ("Bash", {"command": "ollama    pull\tllama3:70b"}),
    ("Bash", {"command": "podman pull docker.io/library/ollama"}),
    ("Bash", {"command": "docker pull ollama/ollama"}),
    ("Bash", {"command": "git lfs pull"}),
    ("Bash", {"command": "git lfs fetch --all"}),
]

MUST_ALLOW = [
    ("Bash", {"command": "git push origin feat/wq625"}),
    ("Read", {"file_path": "/home/lumen/minerva/context/sections/000_preamble.md"}),
    ("Bash", {"command": "bash /home/lumen/minerva/context/claude_context.sh --core"}),
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
    # wq#879: widening the denied-verb leading context must not start matching verbs
    # that are only a substring of a longer identifier or a path.
    ("Bash", {"command": "grep -n shutdown_hook src/app.py"}),
    ("Bash", {"command": "echo my_sudo"}),
    ("Bash", {"command": "cat ./reboot.sh"}),
    # wq#1548: same for the credential re-injection deny — a longer identifier or a
    # path segment ending in the name is not an assignment.
    ("Bash", {"command": "grep -n MY_ANTHROPIC_API_KEY=x src/app.py"}),
    ("Bash", {"command": "cat docs/ANTHROPIC_BASE_URL=notes.md"}),
    # wq#1297: only pull is denied — inspection verbs stay usable from a worker.
    ("Bash", {"command": "ollama list"}),
    ("Bash", {"command": "ollama show llama3:70b"}),
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


# ---- wq#954: the Bash output cap left guard.sh ----
# wq#729 capped Bash by rewriting the command to `{ cmd } 2>&1 | head -c 2000`.
# The cap now rides on BASH_MAX_OUTPUT_LENGTH, set in fworker.build_env() — see
# test_bash_cap.py. What is left to test here is the absence: guard.sh must not
# rewrite a Bash command at all, so no command shape needs an exemption and none
# can escape the cap. The corpus below is wq#729's, both arms merged: what used
# to be capped and what used to be exempted are now treated identically.

FORMERLY_CAPPED = [
    "cat /etc/hostname",
    "find / -maxdepth 4 -iname wq*",
    "git log --oneline -5",
    "grep -rn foo .",
    "make build 2>&1",
    "pytest -q 2>&1 | tail -20",
    "python3 script.py >&2",
    "./run.sh &>/tmp/out.log",
    "diff a b 1>&2",
    'curl -s "http://localhost:8080/x?a=1&b=2"',
]

# The SKIP_CAP corpus: shell-state verbs a pipeline cap could not touch without
# subshelling them away. These are the 168 results / 725 KB the native cap
# recovers (research/UNCAPPED_BASH.md §4) — they must still pass through the
# guard untouched, because now nothing rewrites them and the CLI caps the result.
FORMERLY_EXEMPT = [
    "cd /tmp",
    "cd /tmp && pytest",
    "export FOO=bar",
    "source venv/bin/activate",
    "some-long-job.sh &",
    "sleep 5 & wait",
]


def test_guard_never_rewrites_a_bash_command():
    for cmd in FORMERLY_CAPPED + FORMERLY_EXEMPT:
        rc, stdout, stderr = run_guard_stdout("Bash", {"command": cmd})
        assert rc == 0, (cmd, stderr)
        assert stdout.strip() == "", (cmd, stdout)


def test_must_allow_bash_commands_pass_through_unmodified():
    # cross-check against the existing MUST_ALLOW corpus: allowed Bash calls now
    # reach the tool verbatim. Non-Bash entries keep their own rewrites (Read).
    for tool, inp in MUST_ALLOW:
        if tool != "Bash":
            continue
        rc, stdout, stderr = run_guard_stdout(tool, inp)
        assert rc == 0, (tool, inp, stderr)
        assert stdout.strip() == "", (inp, stdout)


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
    # wq#1316: wq#1285's `merge([[:space:]]|$)` terminator only accepted a space
    # or end-of-string, so every space-free way bash lets the merge token end ran
    # unblocked in a primary. One case per terminator bash actually accepts.
    ({"command": "git merge;true"}, PRIMARY),
    ({"command": "git merge>foo"}, PRIMARY),
    ({"command": "git merge<foo"}, PRIMARY),
    ({"command": "git merge&"}, PRIMARY),
    ({"command": "(git merge)"}, PRIMARY),
    ({"command": 'bash -c "git merge"'}, PRIMARY),
    ({"command": f'bash -c "cd {PRIMARY} && git merge"'}, WORKTREE),
    # wq#1316: the ff-only carve-out is per shell segment — one sanctioned
    # fast-forward must not launder an un-sanctioned merge later in the line.
    ({"command": "git merge --ff-only origin/main && git merge evil"}, PRIMARY),
    ({"command": "git merge --ff-only origin/main; git merge evil"}, PRIMARY),
    ({"command": "git merge --ff-only origin/main | git merge evil"}, PRIMARY),
    # near-miss branch names must not ride the carve-out's terminator class
    ({"command": "git merge --ff-only origin/mainline"}, PRIMARY),
    ({"command": "git merge --ff-only origin/main-deploy"}, PRIMARY),
    # wq#1256 (#19): git clean -f/--force is as destructive as checkout/reset
    ({"command": "git clean -f"}, PRIMARY),
    ({"command": "git clean -fd"}, PRIMARY),
    ({"command": "git clean --force"}, f"{PRIMARY}/harness"),
    ({"command": f"git -C {PRIMARY} clean -fdx"}, WORKTREE),
    # wq#929: the gap that let 2719f89 onto the live main — the branch never moved,
    # a commit was just written onto it, diverging the tree and stalling deploy-ff.
    ({"command": "git commit -m 'fix(dispatcher): register orphan lanes'"}, PRIMARY),
    ({"command": "git commit -am wip"}, f"{PRIMARY}/dispatcher"),
    ({"command": f"git -C {PRIMARY} commit -m x"}, WORKTREE),
    ({"command": f"cd {PRIMARY} && git add -A && git commit -m x"}, WORKTREE),
    ({"command": "git cherry-pick abc1234"}, PRIMARY),
    ({"command": "git revert HEAD"}, PRIMARY),
    ({"command": "git am /tmp/patch.mbox"}, PRIMARY),
    # wq#1335 (§3.3 negative forms): only the exact path-scoped shapes ride the
    # carve-out — whole-tree discard, ref-sourced checkout/restore, escapes, and
    # branch-creating flags all stay denied.
    ({"command": "git checkout -- ."}, PRIMARY),
    ({"command": "git checkout main -- f"}, PRIMARY),
    ({"command": "git restore --source=HEAD~1 f"}, PRIMARY),
    ({"command": "git checkout -- ../escape"}, PRIMARY),
    ({"command": "git checkout -- -b"}, PRIMARY),
    ({"command": "git restore /etc/hosts"}, PRIMARY),
    ({"command": "git checkout -b feat -- f"}, PRIMARY),
    ({"command": f"git -C {PRIMARY}/../foreman checkout -- f"}, WORKTREE),
    # the carve-out is per segment — it must not launder a later verb
    ({"command": "git checkout -- f && git checkout main"}, PRIMARY),
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
    # wq#1256 (#19): a dry-run/status-only clean is not destructive
    ({"command": "git clean -n"}, PRIMARY),
    ({"command": "git clean --dry-run"}, PRIMARY),
    # wq#929: committing in the worker's OWN worktree is the sanctioned path, and
    # lumen-pr (which refuses --primary itself) carries no `git` token to match on
    ({"command": "git commit -m 'fix(guard): deny primary-tree commits'"}, WORKTREE),
    ({"command": "/home/lumen/bin/lumen-pr commit"}, PRIMARY),
    # reading a primary's commit history is still read-only
    ({"command": "git log --oneline origin/main..main"}, PRIMARY),
    # wq#1285 (#19 harden): merge-base/merge-tree are read-only plumbing, not
    # the branch-moving `git merge` — must not trip the block, since sync-live
    # itself (and this guard's own spec) uses merge-base --is-ancestor to
    # verify a commit landed before any resync.
    ({"command": "git merge-base --is-ancestor abc123 origin/main"}, PRIMARY),
    ({"command": "git merge-base main origin/main"}, PRIMARY),
    ({"command": "git merge-tree main origin/main"}, PRIMARY),
    # wq#1316: the plumbing family stays allowed under the hyphen-aware
    # terminator, and the sanctioned deploy ff still passes when the segment
    # ends in something other than a bare space/EOL.
    ({"command": "git merge-file a.txt base.txt b.txt"}, PRIMARY),
    ({"command": "git merge-base --is-ancestor abc123 origin/main; echo done"}, PRIMARY),
    ({"command": "git merge --ff-only origin/main;"}, PRIMARY),
    ({"command": 'bash -c "git merge --ff-only origin/main"'}, PRIMARY),
    ({"command": "git merge --ff-only origin/main && git log --oneline -1"}, PRIMARY),
    # wq#1335 (ISSUE-LOG #38): path-scoped checkout/restore discards working-tree
    # changes to named files, moves no branch/HEAD, and is the exact remedy
    # lumen-pr's land-refusal prescribes. The #38 refused strings verbatim:
    ({"command": f"git -C {PRIMARY} restore dispatcher/health_state.json"}, WORKTREE),
    ({"command": f"git -C {PRIMARY} checkout -- dispatcher/health_state.json"}, WORKTREE),
    ({"command": "git checkout -- harness/guard.sh"}, PRIMARY),
    ({"command": "git restore --staged harness/guard.sh"}, PRIMARY),
    ({"command": "git restore f.txt docs/g.md"}, PRIMARY),
    ({"command": "git checkout -- .gitignore"}, PRIMARY),
    ({"command": "git checkout -- f.txt && git status"}, PRIMARY),
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


# ---- wq#1719: PRIMARY trees are write-frozen (Edit/Write, not just git) ----
# agents.yaml's default add_dirs make every primary repo root reach for Edit/Write,
# not just Read/Bash — closing that gap so a worker can never edit a primary tree's
# files in place, only its own worktree. "a synthetic ticket that tries to Edit
# /home/lumen/foreman/README must be refused and the attempt must not leave dirt."
def test_primary_tree_write_edit_blocked():
    failures = []
    for tool in ("Write", "Edit"):
        for ptree in (PRIMARY, "/home/lumen/bin", "/home/lumen/pokemon"):
            target = f"{ptree}/README.md"
            rc, stderr = run_guard(tool, {"file_path": target, "content": "x"})
            if rc != 2 or "wq#1719" not in stderr:
                failures.append((tool, target, rc, stderr))
    assert not failures, f"expected clean wq#1719 block, got: {failures}"


def test_primary_tree_write_edit_worktree_allowed():
    failures = []
    for tool in ("Write", "Edit"):
        target = f"{WORKTREE}/README.md"
        rc, stderr = run_guard(tool, {"file_path": target, "content": "x"})
        if rc != 0:
            failures.append((tool, target, rc, stderr))
    assert not failures, f"expected exit 0 (allow) in worker's own worktree, got: {failures}"


def test_primary_tree_write_edit_env_override_respected():
    # A tree listed only via FOREMAN_PRIMARY_TREES is guarded too, same as the
    # git-verb wall above.
    rc, stderr = run_guard(
        "Edit", {"file_path": "/srv/custom-primary/f.py", "old_string": "a", "new_string": "b"},
        env_extra={"FOREMAN_PRIMARY_TREES": "/srv/custom-primary"})
    assert rc == 2 and "wq#1719" in stderr, stderr
    rc, _ = run_guard(
        "Edit", {"file_path": f"{PRIMARY}/f.py", "old_string": "a", "new_string": "b"},
        env_extra={"FOREMAN_PRIMARY_TREES": "/srv/custom-primary"})
    assert rc == 0


# ---- wq#1752 (ISSUE-LOG #302, wq#1733 contradiction): kb + shallow nest archive ----
# wq#1733's verify-pass of wq#1719/PR#426 found the Edit/Write wall above missed kb
# and the shallow /home/lumen/projects/nest archive: both are granted as default
# add_dirs (agents.yaml) but neither was reachable through primary_tree_guard's
# STATIC_PRIMARY_TREES or router.LANE_REPO (kb had no entry at all; LANE_REPO's
# "nest" pointed one level deeper, at .../nest/NoLongerEvil-Thermostat). The prior
# wq#1719 tests only ever exercised the stale foreman/bin/pokemon 3-root list, so
# CI passed despite the gap. These target kb/nest specifically, in both the
# fallback list guard.sh carries when FOREMAN_PRIMARY_TREES is unset/empty, and in
# a simulation of the real runtime env fworker sets from
# primary_tree_guard.primary_trees()'s own output.
def test_primary_trees_registry_includes_kb_and_nest(monkeypatch):
    monkeypatch.delenv("FOREMAN_PRIMARY_TREES", raising=False)
    trees = ptg.primary_trees()
    assert "/home/lumen/kb" in trees and "/home/lumen/projects/nest" in trees, trees


def test_primary_tree_write_edit_kb_and_nest_blocked_fallback():
    # FOREMAN_PRIMARY_TREES unset/empty: guard.sh falls back to its own hardcoded
    # list, which must also carry kb/nest, not just the original 3 roots.
    failures = []
    for tool in ("Write", "Edit"):
        for ptree in ("/home/lumen/kb", "/home/lumen/projects/nest"):
            target = f"{ptree}/README.md"
            rc, stderr = run_guard(tool, {"file_path": target, "content": "x"},
                                    env_extra={"FOREMAN_PRIMARY_TREES": ""})
            if rc != 2 or "wq#1719" not in stderr:
                failures.append((tool, target, rc, stderr))
    assert not failures, f"expected clean wq#1719 block, got: {failures}"


def test_primary_tree_write_edit_kb_and_nest_blocked_runtime_env(monkeypatch):
    # wq#1733's actual repro: build FOREMAN_PRIMARY_TREES the way fworker does, from
    # primary_tree_guard.primary_trees()'s own output, and confirm kb/nest are
    # covered by the resulting env value, not just by a test-only override.
    monkeypatch.delenv("FOREMAN_PRIMARY_TREES", raising=False)
    trees = ptg.primary_trees()
    assert "/home/lumen/kb" in trees and "/home/lumen/projects/nest" in trees, trees
    env_val = ":".join(trees)
    failures = []
    for tool in ("Write", "Edit"):
        for ptree in ("/home/lumen/kb", "/home/lumen/projects/nest"):
            target = f"{ptree}/README.md"
            rc, stderr = run_guard(tool, {"file_path": target, "content": "x"},
                                    env_extra={"FOREMAN_PRIMARY_TREES": env_val})
            if rc != 2 or "wq#1719" not in stderr:
                failures.append((tool, target, rc, stderr))
    assert not failures, f"expected clean wq#1719 block, got: {failures}"


# ---- wq#1831: kb/ISSUE-LOG.md is the one sanctioned Edit target in a primary ----
# kb has no remote and no lumen-pr flow, so the wall's "land it with lumen-pr"
# remedy is unreachable there; since wq#1719 every worker's log row has landed via
# shell redirect (ISSUE-LOG #349). The carve-out sanctions Edit — appends and
# status flips — on exactly that file. Write (whole-file replace) stays blocked,
# as does any other kb path and any unnormalized spelling of the log's path.
def test_kb_issue_log_edit_allowed():
    rc, stderr = run_guard(
        "Edit", {"file_path": "/home/lumen/kb/ISSUE-LOG.md",
                 "old_string": "| 478 | x |", "new_string": "| 478 | x |\n| 479 | y |"})
    assert rc == 0, stderr


def test_kb_issue_log_write_still_blocked():
    rc, stderr = run_guard(
        "Write", {"file_path": "/home/lumen/kb/ISSUE-LOG.md", "content": "clobbered"})
    assert rc == 2 and "wq#1719" in stderr, stderr


def test_kb_issue_log_unnormalized_path_still_blocked():
    rc, stderr = run_guard(
        "Edit", {"file_path": "/home/lumen/kb/x/../ISSUE-LOG.md",
                 "old_string": "a", "new_string": "b"})
    assert rc == 2 and "wq#1719" in stderr, stderr


def test_kb_other_files_edit_still_blocked():
    rc, stderr = run_guard(
        "Edit", {"file_path": "/home/lumen/kb/CLAUDE.md",
                 "old_string": "a", "new_string": "b"})
    assert rc == 2 and "wq#1719" in stderr, stderr


def test_primary_tree_branch_switch_blocked_kb_and_nest_fallback():
    # Same fallback-list gap, on the wq#900 git-verb wall (Bash), not just Edit/Write.
    failures = []
    for ptree in ("/home/lumen/kb", "/home/lumen/projects/nest"):
        rc, stderr = run_guard("Bash", {"command": "git checkout feature"}, cwd=ptree,
                                env_extra={"FOREMAN_PRIMARY_TREES": ""})
        if rc != 2 or "wq#900" not in stderr:
            failures.append((ptree, rc, stderr))
    assert not failures, f"expected clean wq#900 block, got: {failures}"


# ---- wq#1761 (ISSUE-LOG #313, wq#1760 contradiction): work-queue ----
# wq#1760's verify-pass of wq#1752/PR#459 found the same bug class survives for a
# third default add_dir: /home/lumen/agents/work-queue (first entry in agents.yaml's
# add_dirs) is granted to every worker but was reachable through neither
# STATIC_PRIMARY_TREES/the guard.sh fallbacks nor router.LANE_REPO — and unlike
# kb/nest it can never be covered by LANE_REPO at all, since it isn't a git repo.
# Confirmed live pre-fix: Write/Edit into work-queue/wq.db (the sqlite store backing
# the fleet task queue every worker reads/writes through the wq CLI) exited 0
# (ALLOWED) — worse than kb/nest since a worker could hand-corrupt the live queue
# directly, bypassing wq semantics entirely.
def test_primary_tree_write_edit_work_queue_blocked_fallback():
    # FOREMAN_PRIMARY_TREES unset/empty: guard.sh's own hardcoded fallback list
    # must carry work-queue, not just the kb/nest pair wq#1752 added.
    failures = []
    for tool in ("Write", "Edit"):
        for target in ("/home/lumen/agents/work-queue/wq.db",
                       "/home/lumen/agents/work-queue/wq",
                       "/home/lumen/agents/work-queue/README.md"):
            rc, stderr = run_guard(tool, {"file_path": target, "content": "x"},
                                    env_extra={"FOREMAN_PRIMARY_TREES": ""})
            if rc != 2 or "wq#1719" not in stderr:
                failures.append((tool, target, rc, stderr))
    assert not failures, f"expected clean wq#1719 block, got: {failures}"


def test_primary_tree_write_edit_work_queue_blocked_runtime_env(monkeypatch):
    # wq#1760's actual repro: build FOREMAN_PRIMARY_TREES the way fworker does, from
    # primary_tree_guard.primary_trees()'s own output, and confirm work-queue is
    # covered by the resulting env value, not just by a test-only override.
    monkeypatch.delenv("FOREMAN_PRIMARY_TREES", raising=False)
    trees = ptg.primary_trees()
    assert "/home/lumen/agents/work-queue" in trees, trees
    env_val = ":".join(trees)
    failures = []
    for tool in ("Write", "Edit"):
        target = "/home/lumen/agents/work-queue/wq.db"
        rc, stderr = run_guard(tool, {"file_path": target, "content": "x"},
                                env_extra={"FOREMAN_PRIMARY_TREES": env_val})
        if rc != 2 or "wq#1719" not in stderr:
            failures.append((tool, target, rc, stderr))
    assert not failures, f"expected clean wq#1719 block, got: {failures}"


def test_primary_tree_branch_switch_blocked_work_queue_fallback():
    # Same fallback-list gap, on the wq#900 git-verb wall (Bash), not just Edit/Write.
    # work-queue isn't itself a git repo, but a worker could still run a git verb
    # with --git-dir/--work-tree/-C pointed at it, or simply have cwd resolve there.
    rc, stderr = run_guard("Bash", {"command": "git checkout feature"},
                            cwd="/home/lumen/agents/work-queue",
                            env_extra={"FOREMAN_PRIMARY_TREES": ""})
    assert rc == 2 and "wq#900" in stderr, stderr
