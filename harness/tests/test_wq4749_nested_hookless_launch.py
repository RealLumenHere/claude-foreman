"""wq#4749 (wq#4744 T5): guard.sh denies a nested Claude launch that would drop hooks,
and worker-settings.json disables the bypass permission mode. Zero quota.

The two literals tests/test_settings_lint.py bans inside harness/ are built at runtime.
"""
import json
import os
import subprocess
import tempfile

import pytest

HARNESS = os.path.join(os.path.dirname(__file__), "..")
GUARD = os.path.join(HARNESS, "guard.sh")
WORKTREE = "/home/lumen/worktrees/wq999-a1"
SKIP_FLAG = "--dangerously-skip-" + "permissions"
BYPASS_MODE = "bypass" + "Permissions"


def run_bash(command):
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": "test-session",
        "cwd": WORKTREE,
        "effort": {"level": "high"},
    })
    env = dict(os.environ)
    env["FOREMAN_WORKER"] = "test-worker"
    env["FOREMAN_EVENTS_DIR"] = tempfile.mkdtemp(prefix="guard_test_events_")
    env["FOREMAN_WORKTREE_ROOT"] = WORKTREE
    p = subprocess.run([GUARD], input=payload, capture_output=True, text=True, env=env)
    return p.returncode, p.stderr


def test_worker_settings_disable_bypass_mode():
    with open(os.path.join(HARNESS, "worker-settings.json")) as f:
        perms = json.load(f)["permissions"]
    assert perms["disableBypassPermissionsMode"] == "disable"


DENY = [
    "claude --safe-mode -p hi",
    "npx @anthropic-ai/claude-code --restricted -p hi",
    "node /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js --bare -p hi",
    "claude --bare",
    "CLAUDE_CODE_SAFE_MODE=1 claude -p hi",
    "env CLAUDE_CODE_RESTRICTED=1 node cli.js -p hi",
    "export CLAUDE_CODE_SAFE_MODE=1",
    # quote-split and bash -c / ssh wrapping
    'env "CLAUDE_CODE_SAFE""_MODE=1" claude -p hi',
    "bash -c 'claude --safe-mode -p hi'",
    "ssh host 'npx claude --bare'",
    "claude\t--restricted   -p hi",
    "claude '--sa''fe-mode' -p hi",
    "claude --settings '{\"disableAllHooks\": true}' -p hi",
    "claude " + SKIP_FLAG + " -p hi",
    "claude --permission-mode " + BYPASS_MODE + " -p hi",
    "claude --permission-mode=" + BYPASS_MODE + " -p hi",
    "npx claude --allow" + SKIP_FLAG[1:] + " -p hi",
]


@pytest.mark.parametrize("cmd", DENY)
def test_nested_hookless_launch_denied(cmd):
    rc, err = run_bash(cmd)
    assert rc == 2, (cmd, err)
    assert "wq#4749" in err, (cmd, err)


ALLOW = [
    "git clone --bare /home/lumen/foreman /tmp/fworker-wq999/mirror.git",
    "git init --bare /tmp/fworker-wq999/x.git",
    "node --version",
    "npx tsc --noEmit",
    "claude --version",
    "claude mcp list",
    "grep -n disableBypassPermissionsMode harness/worker-settings.json",
    "python3 -m pytest harness/tests/test_wq4749_nested_hookless_launch.py -q",
    # prose inside a lone wq call stays exempt, same as the denied-verb scan
    '/home/lumen/bin/wq done 4749 --notes "guard denies claude --safe-mode and --bare"',
]


@pytest.mark.parametrize("cmd", ALLOW)
def test_ordinary_commands_allowed(cmd):
    rc, err = run_bash(cmd)
    assert rc == 0, (cmd, err)


AUDIT = os.path.join(HARNESS, "claude_settings_audit.py")


def run_audit(*paths):
    p = subprocess.run(["python3", AUDIT, *paths], capture_output=True, text=True)
    return p.returncode, p.stdout


def test_audit_clean_settings_has_no_findings(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({
        # wq#4912: user scope does not enforce disableBypassPermissionsMode, so a clean
        # user-scope file does not carry it (setting it there is itself a finding).
        "permissions": {"defaultMode": "acceptEdits"},
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": "/home/lumen/foreman/harness/guard.sh"}]}]},
    }))
    rc, out = run_audit(str(f))
    rep = json.loads(out)[0]
    assert rc == 0 and rep["findings"] == [], out
    assert rep["hooks"]["PreToolUse"][0]["commands"] == ["/home/lumen/foreman/harness/guard.sh"]


def test_audit_flags_hookless_posture_and_masks_values(tmp_path):
    token = "gh" "p_" + "A" * 36
    env_value = "z" * 30
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({
        "permissions": {"defaultMode": BYPASS_MODE, "allow": ["Bash(git *)"]},
        "skipDangerousModePermissionPrompt": True,
        "disableAllHooks": True,
        "env": {"CLAUDE_CODE_SAFE_MODE": "1", "SOME_VALUE": env_value},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify --t " + token}]}]},
    }))
    rc, out = run_audit(str(f), str(tmp_path / "missing.json"))
    reps = json.loads(out)
    findings = " | ".join(reps[0]["findings"])
    assert rc == 1, out
    # wq#4912: an absent disableBypassPermissionsMode is no longer a user-scope finding.
    for needle in ("bypass mode", "skipDangerous",
                   "disableAllHooks", "CLAUDE_CODE_SAFE_MODE", "no PreToolUse"):
        assert needle in findings, (needle, findings)
    assert token not in out and env_value not in out
    assert reps[0]["env_names"] == ["CLAUDE_CODE_SAFE_MODE", "SOME_VALUE"]
    assert reps[1] == {"path": str(tmp_path / "missing.json"), "exists": False}


def test_audit_unparseable_file_exits_2_without_echoing_content(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text("{not json " + "y" * 20)
    rc, out = run_audit(str(f))
    assert rc == 2 and "y" * 20 not in out, out


# ---------------- wq#4912 / wq#5029: managed-scope audit ----------------

def _csa():
    import sys
    if HARNESS not in sys.path:
        sys.path.insert(0, HARNESS)
    import claude_settings_audit as csa
    return csa


def test_audit_user_scope_disable_bypass_is_flagged_as_not_enforced():
    findings = _csa().audit_settings({"permissions": {"disableBypassPermissionsMode": "disable"}})["findings"]
    assert any("USER scope" in x and "NOT enforced" in x for x in findings), findings


def test_audit_managed_scope_requires_disable_bypass():
    csa = _csa()
    bad = csa.audit_settings({"permissions": {}}, is_managed=True)["findings"]
    assert any("MANAGED" in x and "disableBypassPermissionsMode" in x for x in bad), bad
    good = csa.audit_settings({"permissions": {"disableBypassPermissionsMode": "disable"}},
                              is_managed=True)["findings"]
    assert not any("disableBypassPermissionsMode" in x for x in good), good


def test_audit_missing_managed_settings_file_is_a_finding(tmp_path, monkeypatch):
    csa = _csa()
    p = str(tmp_path / "managed-settings.json")
    monkeypatch.setattr(csa, "MANAGED_PATHS", frozenset({p}))
    rep = csa.audit_file(p)
    assert rep["exists"] is False
    assert any("managed-settings.json does not exist" in x for x in rep["findings"]), rep
    # a missing NON-managed file stays a bare {exists: False}
    other = str(tmp_path / "other.json")
    assert csa.audit_file(other) == {"path": other, "exists": False}
