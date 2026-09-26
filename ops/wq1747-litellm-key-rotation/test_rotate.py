"""Tests for wq#1968 rotate.py against a fake LiteLLM proxy + temp env files.

No real secrets or systemd calls are ever touched: the proxy is an in-process
HTTP server on 127.0.0.1, unit restarts go through a monkeypatched
subprocess.run, and every path is under pytest's tmp_path.
"""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

import pytest

import rotate


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class FakeProxyState:
    def __init__(self):
        self.aliases: dict[str, dict] = {}
        self.regenerate_calls: dict[str, int] = {}
        self.chat_calls = 0

    def add(self, alias: str, raw: str, models: list[str] | None = None):
        self.aliases[alias] = {"raw": raw, "models": models or ["gpt-4o-mini"]}
        self.regenerate_calls[alias] = 0

    def find_by_raw(self, raw: str) -> tuple[str, dict] | None:
        for alias, entry in self.aliases.items():
            if entry["raw"] == raw:
                return alias, entry
        return None

    def any_raw_valid(self, raw: str) -> bool:
        return any(e["raw"] == raw for e in self.aliases.values())


def make_handler(state: FakeProxyState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/key/list":
                qs = parse_qs(parsed.query)
                alias = qs.get("key_alias", [None])[0]
                entry = state.aliases.get(alias)
                keys = []
                if entry is not None:
                    keys.append({
                        "key_alias": alias,
                        "token": sha256(entry["raw"]),
                        "models": entry["models"],
                    })
                self._json({"keys": keys, "total_pages": 1})
                return
            self._json({"error": "not found"}, 404)

        def do_POST(self):
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            if parsed.path.startswith("/key/") and parsed.path.endswith("/regenerate"):
                old_raw = unquote(parsed.path[len("/key/"):-len("/regenerate")])
                found = state.find_by_raw(old_raw)
                if found is None:
                    self._json({"error": "key not found"}, 404)
                    return
                alias, entry = found
                state.regenerate_calls[alias] += 1
                new_raw = f"sk-new-{alias}-{state.regenerate_calls[alias]}"
                entry["raw"] = new_raw
                self._json({"key": new_raw, "key_alias": alias, "models": entry["models"]})
                return
            if parsed.path == "/chat/completions":
                state.chat_calls += 1
                auth = self.headers.get("Authorization", "")
                raw = auth.removeprefix("Bearer ")
                if state.any_raw_valid(raw):
                    self._json({"choices": [{"message": {"content": "pong"}}]})
                else:
                    self._json({"error": "invalid key"}, 401)
                return
            self._json({"error": "not found"}, 404)

    return Handler


@pytest.fixture()
def fake_proxy():
    state = FakeProxyState()
    server = HTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    yield state, base_url
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture()
def restart_calls(monkeypatch):
    calls = []

    def fake_run(cmd, check=True, capture_output=True):
        calls.append(cmd)

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(rotate.subprocess, "run", fake_run)
    return calls


def make_args(tmp_path, base_url, **overrides):
    home = tmp_path / "home"
    unit_dir = tmp_path / "units"
    state_dir = tmp_path / "state"
    home.mkdir(exist_ok=True)
    unit_dir.mkdir(exist_ok=True)

    class Args:
        pass

    a = Args()
    a.base_url = base_url
    a.search_roots = [home]
    a.exclude = set(rotate.DEFAULT_EXCLUDE_NAMES)
    a.unit_dirs = [unit_dir]
    a.state_dir = state_dir
    a.never_restart_file = tmp_path / "never-restart-units.txt"
    a.verify_model = None
    a.dry_run = False
    for k, v in overrides.items():
        setattr(a, k, v)
    return a, home, unit_dir


def test_rotate_one_happy_path(tmp_path, fake_proxy, restart_calls):
    state, base_url = fake_proxy
    args, home, unit_dir = make_args(tmp_path, base_url)

    old_raw = "sk-oldvalue123456"
    state.add("test-alias", old_raw)

    env_file = home / "service.env"
    env_file.write_text(f"LITELLM_MASTER_KEY={old_raw}\n")
    env_file.chmod(0o600)

    unit_file = unit_dir / "myservice.service"
    unit_file.write_text(f"[Service]\nEnvironmentFile={env_file}\n")

    result = rotate.rotate_one("test-alias", args, "test-master-key")

    assert result["status"] == "rotated"
    assert old_raw not in env_file.read_text()
    assert "sk-" + "new-test-alias-1" in env_file.read_text()
    assert oct(env_file.stat().st_mode)[-3:] == "600"
    assert result["units_restarted"] == ["myservice.service"]
    assert result["verify_ok"] is True
    assert len(restart_calls) == 1
    assert "myservice.service" in restart_calls[0]

    # idempotent re-run: no second regenerate call, no second restart
    result2 = rotate.rotate_one("test-alias", args, "test-master-key")
    assert result2["status"] == "already_rotated"
    assert state.regenerate_calls["test-alias"] == 1
    assert len(restart_calls) == 1


def test_dry_run_makes_no_changes(tmp_path, fake_proxy, restart_calls):
    state, base_url = fake_proxy
    args, home, unit_dir = make_args(tmp_path, base_url, dry_run=True)

    old_raw = "sk-dryrunvalue1234"
    state.add("dry-alias", old_raw)
    env_file = home / "service.env"
    env_file.write_text(f"LITELLM_MASTER_KEY={old_raw}\n")

    result = rotate.rotate_one("dry-alias", args, "test-master-key")

    assert result["status"] == "dry_run"
    assert str(env_file) in result["files_would_rewrite"]
    assert env_file.read_text() == f"LITELLM_MASTER_KEY={old_raw}\n"
    assert state.regenerate_calls["dry-alias"] == 0
    assert restart_calls == []


def test_alias_not_found(tmp_path, fake_proxy):
    _state, base_url = fake_proxy
    args, _home, _unit_dir = make_args(tmp_path, base_url)

    result = rotate.rotate_one("nope", args, "test-master-key")
    assert result["status"] == "alias_not_found"


def test_old_value_not_found_on_disk(tmp_path, fake_proxy):
    state, base_url = fake_proxy
    args, _home, _unit_dir = make_args(tmp_path, base_url)

    state.add("ghost-alias", "sk-" + "notanywhereonthisbox")
    result = rotate.rotate_one("ghost-alias", args, "test-master-key")
    assert result["status"] == "old_value_not_found_on_disk"


def test_never_restart_list_skips_unit(tmp_path, fake_proxy, restart_calls):
    state, base_url = fake_proxy
    args, home, unit_dir = make_args(tmp_path, base_url)
    args.never_restart_file.write_text("myservice.service\n")

    old_raw = "sk-" + "neverrestartval12"
    state.add("skip-alias", old_raw)
    env_file = home / "service.env"
    env_file.write_text(old_raw)
    unit_file = unit_dir / "myservice.service"
    unit_file.write_text(f"[Service]\nEnvironmentFile={env_file}\n")

    result = rotate.rotate_one("skip-alias", args, "test-master-key")

    assert result["status"] == "rotated"
    assert result["units_restarted"] == []
    assert result["units_skipped_never_restart"] == ["myservice.service"]
    assert restart_calls == []
    # file still gets rewritten even though the unit isn't restarted
    assert old_raw not in env_file.read_text()


def test_user_scoped_unit_gets_a_user_scoped_restart(tmp_path, fake_proxy, restart_calls):
    """wq#1977: scope comes from the unit's own dir, not the env file's path."""
    state, base_url = fake_proxy
    args, home, unit_dir = make_args(tmp_path, base_url)

    old_raw = "sk-" + "userscopedvalue12"
    state.add("user-alias", old_raw)

    # Env file lives where this fleet actually keeps them — nowhere near
    # ~/.config/systemd, which is what the old heuristic keyed off.
    env_file = home / ".secrets" / "litellm-service-keys" / "env" / "user-alias.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(f"LITELLM_KEY={old_raw}\n")

    user_unit = unit_dir / "systemd" / "user" / "agent.service"
    user_unit.parent.mkdir(parents=True)
    user_unit.write_text(f"[Service]\nEnvironmentFile={env_file}\n")

    result = rotate.rotate_one("user-alias", args, "test-master-key")

    assert result["status"] == "rotated"
    assert result["units_restarted"] == ["agent.service (--user)"]
    assert len(restart_calls) == 1
    cmd = restart_calls[0]
    assert cmd[:2] == ["systemd-run", "--user"]
    assert cmd[-4:] == ["systemctl", "--user", "restart", "agent.service"]


def test_system_and_user_units_each_get_their_own_scope(tmp_path, fake_proxy, restart_calls):
    state, base_url = fake_proxy
    args, home, unit_dir = make_args(tmp_path, base_url)

    old_raw = "sk-" + "mixedscopevalue12"
    state.add("mixed-alias", old_raw)
    env_file = home / "shared.env"
    env_file.write_text(old_raw)

    (unit_dir / "systemd" / "user").mkdir(parents=True)
    (unit_dir / "systemd" / "user" / "useronly.service").write_text(
        f"[Service]\nEnvironmentFile={env_file}\n")
    (unit_dir / "systemonly.service").write_text(
        f"[Service]\nEnvironmentFile={env_file}\n")

    result = rotate.rotate_one("mixed-alias", args, "test-master-key")

    assert result["units_restarted"] == ["systemonly.service", "useronly.service (--user)"]
    scoped = {cmd[-1]: ("--user" in cmd) for cmd in restart_calls}
    assert scoped == {"systemonly.service": False, "useronly.service": True}


def test_restart_failure_records_state_and_does_not_re_regenerate(tmp_path, fake_proxy, monkeypatch):
    """A failed restart must not cost the alias a second regeneration."""
    state, base_url = fake_proxy
    args, home, unit_dir = make_args(tmp_path, base_url)

    def boom(cmd, check=True, capture_output=True):
        raise rotate.subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(rotate.subprocess, "run", boom)

    old_raw = "sk-" + "restartfailvalue1"
    state.add("fail-alias", old_raw)
    env_file = home / "service.env"
    env_file.write_text(old_raw)
    (unit_dir / "myservice.service").write_text(f"[Service]\nEnvironmentFile={env_file}\n")

    result = rotate.rotate_one("fail-alias", args, "test-master-key")

    assert result["status"] == "rotated"
    assert result["units_restarted"] == []
    assert result["units_failed_restart"] == ["myservice.service"]

    result2 = rotate.rotate_one("fail-alias", args, "test-master-key")
    assert result2["status"] == "already_rotated"
    assert state.regenerate_calls["fail-alias"] == 1


def test_main_cli_exit_code_reflects_failure(tmp_path, fake_proxy, monkeypatch, restart_calls):
    _state, base_url = fake_proxy
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LITELLM_MASTER_KEY", "test-master-key")
    monkeypatch.setattr(Path, "home", lambda: home)

    code = rotate.main([
        "--alias", "missing-alias",
        "--base-url", base_url,
        "--search-root", str(home),
        "--state-dir", str(tmp_path / "state"),
        "--never-restart-file", str(tmp_path / "nrf.txt"),
    ])
    assert code == 1


def test_main_requires_master_key_env(tmp_path, monkeypatch):
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    code = rotate.main(["--alias", "x", "--base-url", "http://127.0.0.1:1"])
    assert code == 2
