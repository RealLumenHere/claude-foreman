#!/usr/bin/env python3
"""wq#4006 — ops/wq4006-rea-security-rescope-a1/install.sh --verify.

MUST fail while rea/rea-maksoor are still registered at user scope (the bug)
or the project-scope artifacts are missing, and MUST pass once both are in
the fixed state.

Runs standalone: python3 ops/tests/test_wq4006_verify.py
Read-only: every case points WQ4006_CLAUDE_JSON / WQ4006_REA_PROJECT_DIR at
a temp path; the real ~/.claude.json and ~/re are never touched.
"""
import json
import os
import subprocess
import tempfile
import unittest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
BUNDLE = os.path.join(REPO, "ops", "wq4006-rea-security-rescope-a1")
INSTALL = os.path.join(BUNDLE, "install.sh")

with open(os.path.join(BUNDLE, "rea-guard")) as _fh:
    REA_GUARD = _fh.read()
with open(os.path.join(BUNDLE, "HOSTILE_INPUT_POLICY.md")) as _fh:
    POLICY = _fh.read()

GOOD_MCP_JSON = """{
  "mcpServers": {
    "rea": {"command": "/x/re/bin/rea-guard", "args": ["mcp"]},
    "rea-maksoor": {"command": "ssh", "args": ["maksoor", "rea", "mcp"]}
  }
}
"""
GOOD_PROVENANCE = json.dumps({
    "package": "rea-agents",
    "version": "6.5.0",
    "integrity": "sha512-deadbeef",
}) + "\n"


def _populate_project_dir(project_dir, *, mcp_json=GOOD_MCP_JSON,
                           provenance=GOOD_PROVENANCE, guard=REA_GUARD,
                           policy=POLICY):
    os.makedirs(os.path.join(project_dir, "bin"), exist_ok=True)
    os.makedirs(os.path.join(project_dir, "common", "tools"), exist_ok=True)
    if guard is not None:
        with open(os.path.join(project_dir, "bin", "rea-guard"), "w") as fh:
            fh.write(guard)
    if policy is not None:
        with open(os.path.join(project_dir, "common", "tools", "HOSTILE_INPUT_POLICY.md"), "w") as fh:
            fh.write(policy)
    if mcp_json is not None:
        with open(os.path.join(project_dir, ".mcp.json"), "w") as fh:
            fh.write(mcp_json)
    if provenance is not None:
        with open(os.path.join(project_dir, "PROVENANCE.json"), "w") as fh:
            fh.write(provenance)


def _verify(claude_json_text, populate_kwargs=None):
    with tempfile.TemporaryDirectory() as tmp:
        claude_json = os.path.join(tmp, ".claude.json")
        if claude_json_text is not None:
            with open(claude_json, "w") as fh:
                fh.write(claude_json_text)
        project_dir = os.path.join(tmp, "re")
        _populate_project_dir(project_dir, **(populate_kwargs or {}))
        env = dict(os.environ, WQ4006_CLAUDE_JSON=claude_json,
                   WQ4006_REA_PROJECT_DIR=project_dir)
        return subprocess.run(["bash", INSTALL, "--verify"], env=env,
                               capture_output=True, text=True)


CLEAN_CLAUDE_JSON = json.dumps({"mcpServers": {"other-tool": {"command": "x"}}}) + "\n"
DRIFTED_CLAUDE_JSON = json.dumps({"mcpServers": {
    "rea": {"command": "rea", "args": ["mcp"]},
    "rea-maksoor": {"command": "ssh"},
    "other-tool": {"command": "x"},
}}) + "\n"


class VerifyContract(unittest.TestCase):
    def test_fixed_state_passes(self):
        r = _verify(CLEAN_CLAUDE_JSON)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("VERIFY OK", r.stdout)

    def test_no_claude_json_still_passes(self):
        r = _verify(None)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_user_scope_still_registered_fails(self):
        r = _verify(DRIFTED_CLAUDE_JSON)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("still in user scope", r.stdout)
        self.assertIn("rea", r.stdout)
        self.assertIn("rea-maksoor", r.stdout)

    def test_malformed_claude_json_errors(self):
        r = _verify("{not json")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_missing_guard_wrapper_fails(self):
        r = _verify(CLEAN_CLAUDE_JSON, {"guard": None})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("MISS absent", r.stdout)
        self.assertIn("rea-guard", r.stdout)

    def test_missing_policy_fails(self):
        r = _verify(CLEAN_CLAUDE_JSON, {"policy": None})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("HOSTILE_INPUT_POLICY.md", r.stdout)

    def test_mcp_json_missing_rea_maksoor_fails(self):
        thin_mcp = json.dumps({"mcpServers": {
            "rea": {"command": "/x/re/bin/rea-guard"},
        }}) + "\n"
        r = _verify(CLEAN_CLAUDE_JSON, {"mcp_json": thin_mcp})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("rea-maksoor", r.stdout)

    def test_mcp_json_not_pointed_at_guard_fails(self):
        unguarded_mcp = json.dumps({"mcpServers": {
            "rea": {"command": "/home/lumen/.npm-global/bin/rea"},
            "rea-maksoor": {"command": "ssh"},
        }}) + "\n"
        r = _verify(CLEAN_CLAUDE_JSON, {"mcp_json": unguarded_mcp})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("rea-guard", r.stdout)

    def test_provenance_missing_integrity_fails(self):
        thin_provenance = json.dumps({"package": "rea-agents", "version": "6.5.0"}) + "\n"
        r = _verify(CLEAN_CLAUDE_JSON, {"provenance": thin_provenance})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("integrity", r.stdout)

    def test_provenance_absent_fails(self):
        r = _verify(CLEAN_CLAUDE_JSON, {"provenance": None})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("PROVENANCE.json", r.stdout)


if __name__ == "__main__":
    unittest.main()
