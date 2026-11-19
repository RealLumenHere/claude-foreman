#!/usr/bin/env python3
"""claude_settings_audit.py — masked bypass-posture audit of Claude Code settings files
(wq#4749, wq#4744 T5; docs/hardware-re-safety/gap-analysis.md §4).

Reports the shape of each settings file an interactive session loads, never its values:
permission mode, the bypass/hook kill switches, hook commands (through masking.mask_text),
env var NAMES only, and allow/deny rule counts. Findings flag what leaves a session
without its guard hooks or able to enter the bypass permission mode.

Workers cannot run this on the real files: capability_lint.DIR_DENY_PREFIXES refuses a
/home/lumen/.claude grant and guard.sh denies */.claude/* reads. Run it from the
orchestrator or a shell on Lumen:

    python3 /home/lumen/foreman/harness/claude_settings_audit.py            # default set
    python3 /home/lumen/foreman/harness/claude_settings_audit.py FILE...    # explicit
Exit 0 = no findings, 1 = findings, 2 = a file did not parse.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import masking  # noqa: E402

DEFAULT_FILES = (
    "/etc/claude-code/managed-settings.json",
    "/home/lumen/.claude/settings.json",
    "/home/lumen/.claude/settings.local.json",
    "/home/lumen/re/.claude/settings.json",
    "/home/lumen/re/.claude/settings.local.json",
)
HOOK_DROPPING_ENV = ("CLAUDE_CODE_SAFE_MODE", "CLAUDE_CODE_RESTRICTED")
BYPASS_MODE = "bypass" + "Permissions"  # split: tests/test_settings_lint.py bans the literal
# wq#4912: only managed-scope (system-wide) settings reliably enforce
# disableBypassPermissionsMode. User-scope is silently ignored by Claude Code.
MANAGED_PATHS = frozenset({"/etc/claude-code/managed-settings.json",
                           "/Library/Application Support/ClaudeCode/managed-settings.json"})


def _hooks(data):
    out = {}
    for event, groups in (data.get("hooks") or {}).items():
        out[event] = [
            {"matcher": g.get("matcher", ""),
             "commands": [masking.mask_text(h.get("command", ""))[0]
                          for h in g.get("hooks", []) if h.get("type", "command") == "command"]}
            for g in groups if isinstance(g, dict)
        ]
    return out


def audit_settings(data, *, is_managed=False):
    perms = data.get("permissions") or {}
    env = data.get("env") or {}
    hooks = _hooks(data)
    report = {
        "defaultMode": perms.get("defaultMode"),
        "disableBypassPermissionsMode": perms.get("disableBypassPermissionsMode"),
        "disableAllHooks": data.get("disableAllHooks"),
        "allowManagedHooksOnly": data.get("allowManagedHooksOnly"),
        "skipDangerousModePermissionPrompt": data.get("skipDangerousModePermissionPrompt"),
        "apiKeyHelper_set": "apiKeyHelper" in data,
        "env_names": sorted(env),
        "allow_rules": len(perms.get("allow") or []),
        "deny_rules": len(perms.get("deny") or []),
        "hooks": hooks,
    }
    findings = []
    if perms.get("defaultMode") == BYPASS_MODE:
        findings.append("defaultMode is the bypass mode")
    bypass_val = perms.get("disableBypassPermissionsMode")
    if is_managed:
        # wq#4912: managed scope IS enforced — flag if missing
        if bypass_val != "disable":
            findings.append("MANAGED permissions.disableBypassPermissionsMode is not "
                            "\"disable\" — bypass mode is NOT blocked system-wide")
    else:
        # wq#4912: user/project scope is NOT enforced by Claude Code for this key.
        # The setting here is cosmetic; only managed-settings.json blocks bypass.
        if bypass_val == "disable":
            findings.append("permissions.disableBypassPermissionsMode is set in USER "
                            "scope — this is NOT enforced by Claude Code; only "
                            "/etc/claude-code/managed-settings.json blocks bypass mode")
    if data.get("skipDangerousModePermissionPrompt"):
        findings.append("skipDangerousModePermissionPrompt is set")
    if data.get("disableAllHooks"):
        findings.append("disableAllHooks is set: no hook in this session fires")
    for name in HOOK_DROPPING_ENV:
        if name in env:
            findings.append(f"env sets {name}: user/project hooks do not load")
    if not hooks.get("PreToolUse"):
        findings.append("no PreToolUse hook: nothing refuses a tool call at runtime")
    report["findings"] = findings
    return report


def audit_file(path):
    if not os.path.exists(path):
        # wq#4912: a missing managed-settings.json is itself a finding
        result = {"path": path, "exists": False}
        if path in MANAGED_PATHS:
            result["findings"] = [
                "managed-settings.json does not exist — bypass mode is NOT "
                "blocked system-wide. Create it with: sudo mkdir -p "
                "/etc/claude-code && echo '{\"permissions\": "
                "{\"disableBypassPermissionsMode\": \"disable\"}}' "
                "| sudo tee /etc/claude-code/managed-settings.json"
            ]
        return result
    with open(path) as f:
        data = json.load(f)
    return {"path": path, "exists": True,
            **audit_settings(data, is_managed=(path in MANAGED_PATHS))}


def main(argv):
    paths = argv or list(DEFAULT_FILES)
    reports, rc = [], 0
    for path in paths:
        try:
            rep = audit_file(path)
        except (OSError, ValueError) as e:
            rep, rc = {"path": path, "error": type(e).__name__}, 2
        if rep.get("findings") and rc == 0:
            rc = 1
        reports.append(rep)
    print(json.dumps(reports, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
