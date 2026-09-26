"""wq#1973 verify-pass repro: rotate.py (wq#1968, PR#602) restart-scope bug.

Shows rotate_one() finds a unit correctly under ~/.config/systemd/user (a
--user unit) but issues a SYSTEM-scope `systemctl restart` (no --user)
because `is_user` (rotate.py:337) is computed from the rewritten env-file's
path, not from which unit_dirs entry the unit was actually found under.
No live proxy/systemd needed: fake HTTP server + monkeypatched
subprocess.run, same technique as the PR's own test_rotate.py.

Run: python3 reports/1973_verify_wq1968_repro_restart_scope.py
"""
import sys
import hashlib
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote

sys.path.insert(0, "/home/lumen/foreman/ops/wq1747-litellm-key-rotation")
import rotate  # noqa: E402


def sha256(v):
    return hashlib.sha256(v.encode()).hexdigest()


class State:
    def __init__(self):
        self.raw = "sk-" + "usersvckey123456"
        self.regen_calls = 0


state = State()


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
        p = urlparse(self.path)
        if p.path == "/key/list":
            self._json({
                "keys": [{
                    "key_alias": "lumen-agent-2",
                    "token": sha256(state.raw),
                    "models": ["gpt-4o-mini"],
                }],
                "total_pages": 1,
            })
            return
        self._json({}, 404)

    def do_POST(self):
        p = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if p.path.startswith("/key/") and p.path.endswith("/regenerate"):
            old = unquote(p.path[len("/key/"):-len("/regenerate")])
            if old != state.raw:
                self._json({"error": "not found"}, 404)
                return
            state.regen_calls += 1
            state.raw = "sk-usersvckey-new-" + str(state.regen_calls)
            self._json({"key": state.raw})
            return
        if p.path == "/chat/completions":
            self._json({"choices": [{"message": {"content": "pong"}}]})
            return
        self._json({}, 404)


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = "http://127.0.0.1:" + str(server.server_port)

    tmp = Path(tempfile.mkdtemp())
    home = tmp / "home"
    home.mkdir()
    # Real fleet layout: ops/wq1594-ops-emergency-recurring-job/agent5-dgm-key.conf:31
    secrets_env_dir = home / ".secrets" / "litellm-service-keys" / "env"
    secrets_env_dir.mkdir(parents=True)
    env_file = secrets_env_dir / "lumen-agent-2.env"
    env_file.write_text("LITELLM_MASTER_KEY=" + state.raw + "\n")

    user_unit_dir = home / ".config" / "systemd" / "user"
    user_unit_dir.mkdir(parents=True)
    unit_file = user_unit_dir / "lumen-agent.service"
    unit_file.write_text("[Service]\nEnvironmentFile=" + str(env_file) + "\n")

    restart_calls = []

    def fake_run(cmd, check=True, capture_output=True):
        restart_calls.append(cmd)

        class R:
            returncode = 0
        return R()

    rotate.subprocess.run = fake_run

    class Args:
        pass

    a = Args()
    a.base_url = base_url
    a.search_roots = [home]
    a.exclude = set(rotate.DEFAULT_EXCLUDE_NAMES)
    a.unit_dirs = [user_unit_dir]  # only the --user unit dir is searched
    a.state_dir = tmp / "state"
    a.never_restart_file = tmp / "nrf.txt"
    a.verify_model = None
    a.dry_run = False

    result = rotate.rotate_one("lumen-agent-2", a, "test-master-key")
    print("result:", result)
    print("restart_calls:", restart_calls)
    assert result["status"] == "rotated"
    assert result["units_restarted"] == ["lumen-agent.service"], result
    cmd = restart_calls[0]
    print("issued command:", cmd)
    if "--user" in cmd:
        print("PASS (unexpected): restarted with --user scope")
    else:
        print(
            "BUG CONFIRMED: unit found only in ~/.config/systemd/user (a "
            "--user unit) but restart_unit used SYSTEM scope (systemctl "
            "restart, no --user) because is_user is computed from the "
            "env-file path, not the unit's directory."
        )
    server.shutdown()


if __name__ == "__main__":
    main()
