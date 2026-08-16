"""Settings pack lint + governor-gate refusal — BUILD_PLAN.md §T2a adversarial verify
items 3 and 4. Zero quota.
"""
import json
import os
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(HARNESS_DIR, "worker-settings.json")


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


def test_docker_ps_logs_allowed_docker_star_denied():
    with open(SETTINGS) as f:
        data = json.load(f)
    allow = data["permissions"]["allow"]
    deny = data["permissions"]["deny"]
    assert "Bash(docker ps*)" in allow
    assert "Bash(docker logs*)" in allow
    assert "Bash(docker *)" in deny


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
