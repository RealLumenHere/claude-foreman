#!/usr/bin/env python3
"""
Tests for the wq#4873 hardened mcp_gateway.py.
Validates: run_shell removal, per-tool auth levels, structured audit logging,
CORS restriction, stale-reference cleanup, argument redaction.

Run: python3 -m pytest test_hardened_gateway.py -v
"""
import json
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ── Load the gateway module from this directory ─────────────────────────────

GW_PATH = Path(__file__).parent / "mcp_gateway.py"


def _load_gateway(env_overrides=None):
    """Import mcp_gateway.py with the given env, returning the module."""
    env = {
        "MCP_GATEWAY_TOKEN": "a" * 40,
        "MCP_GATEWAY_ADMIN_TOKEN": "b" * 40,
    }
    if env_overrides:
        env.update(env_overrides)
    with patch.dict(os.environ, env, clear=False):
        spec = importlib.util.spec_from_file_location("mcp_gateway", GW_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def gw():
    return _load_gateway()


# ── F1: run_shell removed ───────────────────────────────────────────────────

class TestRunShellRemoved:
    def test_no_run_shell_in_tools(self, gw):
        names = [t["name"] for t in gw.TOOLS]
        assert "run_shell" not in names

    def test_no_run_shell_in_handlers(self, gw):
        assert "run_shell" not in gw.TOOL_HANDLERS

    def test_no_run_shell_in_levels(self, gw):
        assert "run_shell" not in gw.TOOL_LEVELS

    def test_source_has_no_shell_true(self):
        src = GW_PATH.read_text()
        assert "shell=True" not in src


# ── F2: per-tool permission levels ──────────────────────────────────────────

class TestPerToolAuth:
    def test_every_tool_has_level(self, gw):
        for t in gw.TOOLS:
            assert t["name"] in gw.TOOL_LEVELS, f"{t['name']} missing from TOOL_LEVELS"

    def test_every_handler_has_level(self, gw):
        for name in gw.TOOL_HANDLERS:
            assert name in gw.TOOL_LEVELS, f"{name} missing from TOOL_LEVELS"

    def test_read_tools_are_read(self, gw):
        read_tools = {"get_queue_status", "get_agent_logs", "get_memory", "get_agent_status"}
        for name in read_tools:
            assert gw.TOOL_LEVELS[name] == "read"

    def test_write_tools_are_write(self, gw):
        write_tools = {"add_task", "clear_failed_tasks"}
        for name in write_tools:
            assert gw.TOOL_LEVELS[name] == "write"

    def test_restart_is_admin(self, gw):
        assert gw.TOOL_LEVELS["restart_agent"] == "admin"


# ── F3: admin gating ───────────────────────────────────────────────────────

class TestAdminGating:
    def test_admin_denied_with_regular_token(self, gw):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            gw.check_tool_access("restart_agent", "a" * 40)
        assert exc.value.status_code == 403

    def test_admin_allowed_with_admin_token(self, gw):
        gw.check_tool_access("restart_agent", "b" * 40)

    def test_admin_disabled_when_no_admin_token(self):
        mod = _load_gateway({"MCP_GATEWAY_ADMIN_TOKEN": ""})
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            mod.check_tool_access("restart_agent", "a" * 40)
        assert exc.value.status_code == 403
        assert "not configured" in exc.value.detail

    def test_read_tool_allowed_with_regular_token(self, gw):
        gw.check_tool_access("get_queue_status", "a" * 40)

    def test_write_tool_allowed_with_regular_token(self, gw):
        gw.check_tool_access("add_task", "a" * 40)


# ── F4: audit logging ──────────────────────────────────────────────────────

class TestAuditLog:
    def test_audit_writes_jsonl(self, gw, tmp_path):
        log_file = tmp_path / "audit.jsonl"
        with patch.object(gw, "AUDIT_LOG_FILE", log_file), \
             patch.object(gw, "AUDIT_LOG_DIR", tmp_path):
            gw.audit_log(tool="get_memory", client_ip="100.117.66.102",
                         level="read", args={"section": "posts"},
                         result_ok=True)
        records = [json.loads(l) for l in log_file.read_text().splitlines()]
        assert len(records) == 1
        r = records[0]
        assert r["tool"] == "get_memory"
        assert r["client_ip"] == "100.117.66.102"
        assert r["level"] == "read"
        assert r["args"] == {"section": "posts"}
        assert r["ok"] is True
        assert "ts" in r

    def test_audit_records_errors(self, gw, tmp_path):
        log_file = tmp_path / "audit.jsonl"
        with patch.object(gw, "AUDIT_LOG_FILE", log_file), \
             patch.object(gw, "AUDIT_LOG_DIR", tmp_path):
            gw.audit_log(tool="add_task", client_ip="1.2.3.4",
                         level="write", args={"topic": "test"},
                         result_ok=False, error="missing field")
        r = json.loads(log_file.read_text().strip())
        assert r["ok"] is False
        assert r["error"] == "missing field"


# ── F5: argument redaction ──────────────────────────────────────────────────

class TestRedaction:
    def test_sensitive_fields_redacted(self, gw):
        result = gw._redact_args({
            "topic": "hello",
            "password": "s3cret",
            "api_key": "sk-12345",
            "Authorization": "Bearer xyz",
        })
        assert result["topic"] == "hello"
        assert result["password"] == "[REDACTED]"
        assert result["api_key"] == "[REDACTED]"
        assert result["Authorization"] == "[REDACTED]"

    def test_long_values_truncated(self, gw):
        long_val = "x" * 1000
        result = gw._redact_args({"data": long_val})
        assert len(result["data"]) < 300
        assert "truncated" in result["data"]


# ── F6: CORS restriction ───────────────────────────────────────────────────

class TestCORS:
    def test_no_wildcard_origins(self, gw):
        assert "*" not in gw.ALLOWED_ORIGINS

    def test_localhost_allowed(self, gw):
        assert any("localhost" in o for o in gw.ALLOWED_ORIGINS)

    def test_tailnet_regex_present(self):
        src = GW_PATH.read_text()
        assert "allow_origin_regex" in src
        assert "100\\." in src


# ── F7: stale references removed ───────────────────────────────────────────

class TestStaleReferences:
    def test_no_ghost_cms(self):
        src = GW_PATH.read_text()
        assert "Ghost CMS" not in src
        assert ":2368" not in src

    def test_no_groq(self):
        src = GW_PATH.read_text()
        assert "groq" not in src.lower() or "groq" not in src

    def test_no_librechat(self):
        src = GW_PATH.read_text()
        assert "librechat" not in src.lower()

    def test_no_token_budget_tool(self, gw):
        names = [t["name"] for t in gw.TOOLS]
        assert "get_token_budget" not in names


# ── F8: startup fail-closed preserved ──────────────────────────────────────

class TestFailClosed:
    def test_burned_token_refused(self):
        with pytest.raises(SystemExit) as exc:
            _load_gateway({"MCP_GATEWAY_TOKEN": "" + "lumen-gateway-2026"})
        assert exc.value.code == 78

    def test_short_token_refused(self):
        with pytest.raises(SystemExit) as exc:
            _load_gateway({"MCP_GATEWAY_TOKEN": "short"})
        assert exc.value.code == 78

    def test_empty_token_refused(self):
        with pytest.raises(SystemExit) as exc:
            _load_gateway({"MCP_GATEWAY_TOKEN": ""})
        assert exc.value.code == 78


# ── F9: tool/handler/definition consistency ─────────────────────────────────

class TestConsistency:
    def test_tools_match_handlers(self, gw):
        tool_names = {t["name"] for t in gw.TOOLS}
        handler_names = set(gw.TOOL_HANDLERS.keys())
        assert tool_names == handler_names

    def test_tools_match_levels(self, gw):
        tool_names = {t["name"] for t in gw.TOOLS}
        level_names = set(gw.TOOL_LEVELS.keys())
        assert tool_names == level_names

    def test_no_datetime_utcnow(self):
        """datetime.utcnow() is deprecated; must use timezone-aware."""
        src = GW_PATH.read_text()
        assert "utcnow()" not in src
