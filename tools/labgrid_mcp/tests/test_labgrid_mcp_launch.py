import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import labgrid_mcp_launch as L  # noqa: E402

LAUNCHER = Path(L.__file__).resolve()
SESSION_A = "alpha-abc123"
SESSION_B = "beta-def456"
FWORKER_LIVE = "fworker-wq9001"
FWORKER_DONE = "fworker-wq9002"

READ_TOOLS = {"coordinator_info", "list_places", "show_place", "who", "list_resources",
              "list_reservations", "wait_for_change", "forward_list"}
# Every tool in the pilot's ssh/flash/metadata/place_delete categories (report tool inventory).
FORBIDDEN_TOOLS = {"ssh_run", "put_file", "get_file", "forward_open", "forward_remote_open",
                   "forward_close", "flash_dfu", "flash_fastboot", "flash_script", "bootstrap",
                   "write_image", "flash_status", "flash_logs", "add_place", "add_place_alias",
                   "delete_place_alias", "set_place_tags", "set_place_comment", "add_place_match",
                   "delete_place", "delete_place_match"}


@pytest.fixture
def wq_db(tmp_path):
    db = tmp_path / "queue.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE agent_sessions(session_id TEXT PRIMARY KEY, base_name TEXT NOT NULL, "
                "host TEXT, pid INTEGER, first_seen TEXT, last_seen TEXT)")
    con.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, claimed_by TEXT, status TEXT)")
    con.executemany("INSERT INTO agent_sessions(session_id, base_name) VALUES(?,?)",
                    [(SESSION_A, "alpha"), (SESSION_B, "beta")])
    con.executemany("INSERT INTO tasks(id, claimed_by, status) VALUES(?,?,?)",
                    [(9001, FWORKER_LIVE, "claimed"), (9002, FWORKER_DONE, "done")])
    con.commit()
    con.close()
    return str(db)


# ---------------------------------------------------------------- identity (F2)

@pytest.mark.parametrize("ident", [SESSION_A, FWORKER_LIVE])
def test_live_identity_accepted(wq_db, ident):
    assert L.resolve_identity({"WQ_AGENT": ident}, wq_db) == ident


@pytest.mark.parametrize("env,why", [
    ({}, "not set"),
    ({"WQ_AGENT": "  "}, "not set"),
    ({"WQ_AGENT": "a/b"}, "usable"),
    ({"WQ_AGENT": "alpha abc"}, "usable"),
    ({"WQ_AGENT": "alpha"}, "neither"),            # bare base name, not the minted session id
    ({"WQ_AGENT": FWORKER_DONE}, "neither"),       # ticket no longer claimed
    ({"WQ_AGENT": "ghost-000000"}, "neither"),
])
def test_identity_refused(wq_db, env, why):
    with pytest.raises(L.LaunchError, match=why):
        L.resolve_identity(env, wq_db)


def test_unreadable_wq_db_refuses(tmp_path):
    with pytest.raises(L.LaunchError, match="cannot read wq db"):
        L.resolve_identity({"WQ_AGENT": SESSION_A}, str(tmp_path / "missing.db"))


# ---------------------------------------------------------------- coordinator (F6)

@pytest.mark.parametrize("raw,want", [
    ("127.0.0.1:20408", "127.0.0.1:20408"),
    ("127.0.0.1", "127.0.0.1:20408"),
    ("localhost:20499", "localhost:20499"),
    ("100.117.66.102:20408", "100.117.66.102:20408"),
    ("[::1]:20408", "[::1]:20408"),
    ("[fd7a:115c:a1e0::5]", "[fd7a:115c:a1e0::5]:20408"),
])
def test_coordinator_loopback_or_tailnet(raw, want):
    assert L.check_coordinator(raw) == want


@pytest.mark.parametrize("raw", [
    "192.168.8.160:20408", "10.0.0.1", "0.0.0.0:20408", "8.8.8.8:20408", "[2001:db8::1]:1",
    "coordinator.example:20408", "::1:20408", "127.0.0.1:0", "127.0.0.1:99999", "127.0.0.1:x",
])
def test_coordinator_refused(raw):
    with pytest.raises(L.LaunchError):
        L.check_coordinator(raw)


# ---------------------------------------------------------------- acquire timeout (F7)

@pytest.mark.parametrize("raw", ["30", "0.5", "10"])
def test_acquire_timeout_ok(raw):
    assert L.check_acquire_timeout(raw) == float(raw)


@pytest.mark.parametrize("raw", ["31", "120", "0", "-1", "nan", "inf", "abc"])
def test_acquire_timeout_refused(raw):
    with pytest.raises(L.LaunchError):
        L.check_acquire_timeout(raw)


# ---------------------------------------------------------------- profiles / env (F4)

def test_profile_allow_strings_match_ticket():
    assert {n: ",".join(p.allow) for n, p in L.PROFILES.items()} == {
        "observer": "read",
        "analyst": "read,acquire,reservation,console",
        "bench": "read,acquire,reservation,power,io,mux,console",
    }
    assert [n for n, p in L.PROFILES.items() if p.readonly] == ["observer"]
    assert {n: p.tools for n, p in L.PROFILES.items()} == {"observer": 8, "analyst": 19, "bench": 26}


def test_build_env_scrubs_inherited_labgrid_vars():
    parent = {"PATH": "/usr/bin", "LG_USERNAME": "shared", "LG_HOSTNAME": "lumen", "LG_PROXY": "x",
              "LABGRID_MCP_ALLOW": "ssh,flash,place_delete", "LABGRID_MCP_SSH_KEYFILE": "/k",
              "LABGRID_MCP_ACQUIRE_TIMEOUT": "120", "LABGRID_MCP_READONLY": "1"}
    env = L.build_env(parent, SESSION_A, L.PROFILES["bench"], "127.0.0.1:20408", 30.0)
    labgrid = {k: v for k, v in env.items() if k.startswith(("LG_", "LABGRID_MCP_"))}
    assert labgrid == {
        "LG_USERNAME": SESSION_A, "LG_HOSTNAME": f"wq-{SESSION_A}", "LG_COORDINATOR": "127.0.0.1:20408",
        "LABGRID_MCP_ALLOW": "read,acquire,reservation,power,io,mux,console",
        "LABGRID_MCP_ACQUIRE_TIMEOUT": "30",
    }
    assert env["PATH"] == "/usr/bin"


def test_observer_sets_readonly():
    env = L.build_env({}, SESSION_A, L.PROFILES["observer"], "127.0.0.1:20408", 30.0)
    assert env["LABGRID_MCP_READONLY"] == "1" and env["LABGRID_MCP_ALLOW"] == "read"


@pytest.mark.parametrize("cat", sorted(L.FORBIDDEN_CATEGORIES))
def test_forbidden_category_refused(cat):
    with pytest.raises(L.LaunchError, match="forbidden"):
        L.build_env({}, SESSION_A, L.Profile(False, ("read", cat), 0), "127.0.0.1:20408", 30.0)


# ---------------------------------------------------------------- packaging

def test_lock_pins_wheel_hash():
    text = L.LOCK_FILE.read_text()
    L.check_lock_pins_wheel(text)
    with pytest.raises(L.LaunchError):
        L.check_lock_pins_wheel(text.replace(L.WHEEL_SHA256, "0" * 64))
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith(("#", " "))]
    hashes = [ln for ln in text.splitlines() if ln.strip().startswith("--hash=sha256:")]
    assert len(lines) == len(hashes) and all("==" in ln for ln in lines)


def test_check_venv_refuses_missing_and_stale(tmp_path):
    with pytest.raises(L.LaunchError, match="missing"):
        L.check_venv(tmp_path)
    exe = tmp_path / "bin" / "labgrid-mcp"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    (tmp_path / L.STAMP_NAME).write_text("stale\n")
    with pytest.raises(L.LaunchError, match="not built from"):
        L.check_venv(tmp_path)


def test_mcp_config_is_gate_classifiable(capsys):
    assert L.main(["mcp-config", "--profile", "analyst"]) == 0
    servers = json.loads(capsys.readouterr().out)["mcpServers"]
    (name, entry), = servers.items()
    assert "labgrid" in name  # action_gate.classify_mcp_tool's labgrid branch keys on this
    assert entry["command"] == L.LAUNCH_BIN and entry["args"] == ["serve", "--profile", "analyst"]
    assert L.main(["mcp-config", "--profile", "bench", "--coordinator", "192.168.8.160:20408"]) == 2


def _launch_env(wq_db, tmp_path, ident, venv):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LG_", "LABGRID_MCP_", "WQ_"))}
    env.update({"WQ_DB": wq_db, "XDG_RUNTIME_DIR": str(tmp_path), "LABGRID_MCP_LAUNCH_VENV": str(venv),
                # a parent leaking the pilot's dangerous defaults must not reach the server
                "LABGRID_MCP_ALLOW": "read,ssh,flash,metadata,place_delete", "LG_USERNAME": "shared"})
    if ident:
        env["WQ_AGENT"] = ident
    return env


def test_serve_without_identity_refuses_and_keeps_stdout_clean(wq_db, tmp_path):
    r = subprocess.run([sys.executable, str(LAUNCHER), "serve", "--profile", "observer"],
                       env=_launch_env(wq_db, tmp_path, None, tmp_path / "venv"),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and r.stdout == "" and "WQ_AGENT is not set" in r.stderr


# ---------------------------------------------------------------- live server (needs a built venv)

VENV = Path(os.environ.get("LABGRID_MCP_LAUNCH_VENV") or L.DEFAULT_VENV)


def _venv_ready():
    try:
        L.check_venv(VENV)
        return True
    except L.LaunchError:
        return False


live = pytest.mark.skipif(not _venv_ready(), reason=f"no built venv at {VENV} (labgrid-mcp-launch install)")


class McpClient:
    def __init__(self, env, profile):
        self.proc = subprocess.Popen([sys.executable, str(LAUNCHER), "serve", "--profile", profile],
                                     env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        self.lines = queue.Queue()
        threading.Thread(target=lambda: [self.lines.put(ln) for ln in self.proc.stdout], daemon=True).start()
        self.next_id = 0

    def call(self, method, params=None):
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        while True:
            msg = json.loads(self.lines.get(timeout=30))
            if msg.get("id") == self.next_id:
                assert "error" not in msg, msg
                return msg["result"]

    def _send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def tool_names(self):
        self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "wq4908-test", "version": "0"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        names, cursor = set(), None
        while True:
            page = self.call("tools/list", {"cursor": cursor} if cursor else {})
            names |= {t["name"] for t in page["tools"]}
            cursor = page.get("nextCursor")
            if not cursor:
                return names

    def close(self):
        self.proc.kill()
        self.proc.wait(timeout=10)
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.proc.stderr.close()


@live
@pytest.mark.parametrize("profile", sorted(L.PROFILES))
def test_live_profile_tool_counts(wq_db, tmp_path, profile):
    client = McpClient(_launch_env(wq_db, tmp_path, SESSION_A, VENV), profile)
    try:
        names = client.tool_names()
    finally:
        client.close()
    assert len(names) == L.PROFILES[profile].tools, sorted(names)
    assert not names & FORBIDDEN_TOOLS
    assert READ_TOOLS <= names
    if profile == "observer":
        assert names == READ_TOOLS


@live
def test_live_one_server_per_identity(wq_db, tmp_path):
    first = McpClient(_launch_env(wq_db, tmp_path, SESSION_A, VENV), "observer")
    try:
        assert first.tool_names()  # exec'd and serving, so it holds the identity lock
        dup = subprocess.run([sys.executable, str(LAUNCHER), "serve", "--profile", "observer"],
                             env=_launch_env(wq_db, tmp_path, SESSION_A, VENV),
                             capture_output=True, text=True, timeout=30)
        assert dup.returncode == 2 and dup.stdout == "" and "already serving" in dup.stderr
        other = McpClient(_launch_env(wq_db, tmp_path, SESSION_B, VENV), "observer")
        try:
            assert other.tool_names()
        finally:
            other.close()
    finally:
        first.close()
    # the kernel drops the flock with the dead server; the identity is usable again
    again = McpClient(_launch_env(wq_db, tmp_path, SESSION_A, VENV), "observer")
    try:
        assert again.tool_names()
    finally:
        again.close()
