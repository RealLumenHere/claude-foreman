"""Settings pack lint + governor-gate refusal — BUILD_PLAN.md §T2a adversarial verify
items 3 and 4. Zero quota.

Run from the foreman repo root with the harness on the path, or conftest.py cannot
import fworker (46 collection errors):
    cd /home/lumen/foreman && PYTHONPATH=harness:. python3 -m pytest harness/tests/test_settings_lint.py -q
"""
import json
import os
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(HARNESS_DIR, "worker-settings.json")
BASE_PROFILE = os.path.join(HARNESS_DIR, "permission-profiles", "base.json")


def test_settings_json_parses():
    with open(SETTINGS) as f:
        data = json.load(f)
    assert "permissions" in data
    assert "hooks" in data


def test_mode_is_accept_edits():
    with open(SETTINGS) as f:
        data = json.load(f)
    assert data["permissions"]["defaultMode"] == "acceptEdits"


def test_no_bypass_permissions_anywhere_in_harness():
    self_path = os.path.abspath(__file__)
    for root, _, files in os.walk(HARNESS_DIR):
        if "__pycache__" in root or ".pytest_cache" in root:
            continue
        for fn in files:
            path = os.path.join(root, fn)
            if os.path.abspath(path) == self_path or fn.endswith(".md"):
                # this file legitimately names the string it's asserting against;
                # docs (.md) legitimately discuss it in prose too.
                continue
            try:
                with open(path, "r", errors="ignore") as f:
                    content = f.read()
            except (UnicodeDecodeError, IsADirectoryError):
                continue
            assert "bypassPermissions" not in content, f"bypassPermissions found in {path}"
            assert "dangerously-skip-permissions" not in content, f"skip-permissions flag found in {path}"


def test_guard_hook_referenced_and_executable():
    with open(SETTINGS) as f:
        data = json.load(f)
    pre = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert os.path.isfile(pre)
    assert os.access(pre, os.X_OK)


def test_docker_blanket_denied_no_dead_allows():
    # wq#4682 (ebe5f012): Claude Code deny rules take precedence over allow rules,
    # so Bash(docker ps*)/Bash(docker logs*) allows were dead under the blanket
    # Bash(docker *) deny and were removed. Assert the deny stays and no docker
    # allow is reintroduced (it would be shadowed, i.e. misleading). wq#4904.
    with open(SETTINGS) as f:
        data = json.load(f)
    allow = data["permissions"]["allow"]
    deny = data["permissions"]["deny"]
    assert "Bash(docker *)" in deny
    assert not [a for a in allow if a.startswith("Bash(docker")], (
        "docker allow rules are shadowed by the Bash(docker *) deny; do not add them"
    )


def test_credential_paths_denied():
    with open(SETTINGS) as f:
        data = json.load(f)
    deny = data["permissions"]["deny"]
    assert any(".env" in d for d in deny)
    assert any(".credentials.json" in d for d in deny)


def test_oauth_token_secrets_dir_denied():
    # wq#616: worker-settings.json deny list is defense-in-depth on top of guard.sh's
    # fail-closed hook — both must cover the new static-token secrets dir.
    with open(SETTINGS) as f:
        data = json.load(f)
    deny = data["permissions"]["deny"]
    assert any(".secrets" in d for d in deny)


def test_fworker_loads_this_pack_not_the_general_one():
    """wq#1107 was misdiagnosed for a day because ../worker-settings-general.json (a
    separate blanket-`Bash` pack, used by orchestrator-spawned fleet-triage workers) was
    read as if it were the one fworker loads. Pin the real default so the next reader
    checks the file that actually gates a build worker."""
    sys.path.insert(0, HARNESS_DIR)
    import fworker  # noqa: PLC0415 — deliberately late, after the path insert
    assert os.path.realpath(fworker.SETTINGS_FILE) == os.path.realpath(SETTINGS)


def test_readonly_diagnostic_shapes_are_allowlisted():
    """wq#1107: the allowlist is per-command, so every read-only shape a worker actually
    reaches for has to be on it or headless gets an unapprovable "requires approval".
    These three are the shapes with recorded loop-break evidence (2026-08-29)."""
    with open(SETTINGS) as f:
        allow = json.load(f)["permissions"]["allow"]
    for prefix in ("Bash(ps -p ", "Bash(gh pr view", "Bash(jq "):
        assert any(a.startswith(prefix) for a in allow), prefix


def test_process_inspection_is_pid_targeted_not_a_full_dump():
    """Instinct 3 names "ps with args" as credential-emitting: another process's argv can
    carry a bearer token, so a broad `ps aux`/`ps -ef` dump stays off the allowlist. The
    shape wq#1107 needed — liveness/etime for a PID this worker already owns — is
    `ps -p <pid> ...`, which is targeted and does not sweep the process table."""
    with open(SETTINGS) as f:
        allow = json.load(f)["permissions"]["allow"]
    assert "Bash(ps *)" not in allow
    assert "Bash(ps -p *)" in allow


def test_unit_restart_goes_through_lumen_run_not_a_bare_systemctl():
    """wq#1350 (wq#1347 follow-up): `Bash(systemctl restart lumen-messenger)` sits on both
    packs since wq#1323, but a worker that runs it bare still gets polkit's "Access denied
    ... requires interactive authentication" — the worker is lumen, not root, and the
    escalation verb is denied in this same profile. So that entry never restarts anything
    on its own; the detached transient unit is what does, and it has to stay allow-listed
    in BOTH packs or every mutating-systemd ticket becomes unrunnable.

    Live on 2026-09-20 from this lane: the bare form exited 1 on polkit, then
    `lumen-run start --root -- systemctl restart lumen-messenger` moved lumen-messenger
    MainPID 1307246 -> 3271120 with /health back at 200."""
    with open(SETTINGS) as f:
        settings_allow = json.load(f)["permissions"]["allow"]
    with open(BASE_PROFILE) as f:
        base = json.load(f)
    for allow in (settings_allow, base["allow"]):
        assert "Bash(/home/lumen/bin/lumen-run *)" in allow
    assert "Bash(sudo *)" in base["deny"]
    # the comment carries the workaround to whoever reads the inert-looking allow entry
    assert "Bash(systemctl restart lumen-messenger)" in base["allow"]
    assert "lumen-run start --root" in base["_comment"]


def test_env_dump_denied_not_merely_unlisted():
    """Instinct 3 (mask before print): an unlisted command yields an approval prompt no
    headless worker can answer, which is a loop-break; an explicit deny yields a clear
    refusal. Credential-dumping shapes get the explicit deny."""
    with open(SETTINGS) as f:
        deny = json.load(f)["permissions"]["deny"]
    assert "Bash(env)" in deny
    assert any(d.startswith("Bash(printenv") for d in deny)


def test_worker_settings_interim_removed():
    interim = os.path.join(os.path.dirname(HARNESS_DIR), "worker-settings-interim.json")
    assert not os.path.exists(interim), (
        "worker-settings-interim.json should be deleted — superseded by harness/worker-settings.json")


def test_gh_lane_worker_off_bypass_permissions():
    path = "/home/lumen/bin/gh-lane-worker"
    with open(path) as f:
        content = f.read()
    assert "bypassPermissions" not in content
    assert "--settings" in content and "worker-settings.json" in content


def test_can_dispatch_deny_blocks_spawn(tmp_path):
    fake_governor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_governor.py")
    deny_governor = tmp_path / "deny_governor.py"
    deny_governor.write_text(
        "import sys\n"
        "if len(sys.argv) >= 2 and sys.argv[1] == 'can-dispatch':\n"
        "    print('{\"allow\": false, \"local_only\": true, \"reasons\": [\"floor breach\"]}')\n"
        "    sys.exit(1)\n"
    )
    env = dict(os.environ)
    env["FOREMAN_GOVERNOR_CLI"] = str(deny_governor)
    sys.path.insert(0, HARNESS_DIR)
    import importlib
    import fworker
    importlib.reload(fworker)
    fworker.GOVERNOR_CLI = str(deny_governor)
    fworker.STOP_FILE = str(tmp_path / "STOP_nonexistent")
    allow, reason = fworker.can_dispatch("claude-sonnet-5")
    assert allow is False


def test_stop_file_blocks_dispatch(tmp_path):
    sys.path.insert(0, HARNESS_DIR)
    import importlib
    import fworker
    importlib.reload(fworker)
    stop_file = tmp_path / "STOP"
    stop_file.write_text("halt")
    fworker.STOP_FILE = str(stop_file)
    allow, reason = fworker.can_dispatch("claude-sonnet-5")
    assert allow is False
    assert "STOP" in reason
