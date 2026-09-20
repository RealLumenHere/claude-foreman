"""wq#2278 (token-engine T10a + T10e). Zero quota, no `claude -p`.

(a) build_env's local-rung ANTHROPIC_AUTH_TOKEN comes from a PER-TICKET key
    minted by the token-engine broker (duration + budget), read via the 0640
    handoff file the broker's frame points at — the frame itself never carries
    the key value. Broker down/refusing -> the static LITELLM_KEY_FILE exactly
    as before.
(e) CLAUDE_CODE_OAUTH_TOKEN stays in worker env (no provider-side short-lived
    option) and build_env's pop-then-set-EXACTLY-ONE discipline at the
    env.pop block holds for both pools.
"""
import json
import os
import socket
import sys
import threading

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402

CRED_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
MINTED = "sk-minted-per-ticket-key-0000000001"
STATIC = "sk-static-longlived-key-0000000001"


def _setup(tmp_path, monkeypatch, sock_path=None):
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(tmp_path / "runs"))
    static_file = tmp_path / "static.token"
    static_file.write_text(STATIC + "\n")
    monkeypatch.setattr(fworker, "LITELLM_KEY_FILE", str(static_file))
    monkeypatch.setattr(fworker, "TOKEN_ENGINE_SOCK",
                        sock_path or str(tmp_path / "no-such-broker.sock"))
    oauth = tmp_path / "oauth.token"
    oauth.write_text("sk-ant-oat01-dummy")
    monkeypatch.setattr(fworker, "OAUTH_TOKEN_FILE", str(oauth))


def fake_broker(tmp_path, frames):
    """One-shot AF_UNIX broker speaking the te_client wire contract; records
    the request and answers from `frames`. Returns the socket path."""
    sock_path = str(tmp_path / "broker.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(4)
    seen = []

    def serve():
        for frame in frames:
            conn, _ = srv.accept()
            buf = b""
            while b"\n" not in buf:
                buf += conn.recv(4096)
            seen.append(json.loads(buf.decode()))
            conn.sendall((json.dumps(frame) + "\n").encode())
            conn.close()
        srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return sock_path, seen


def ok_frame(result):
    return {"ok": True, "verb": "gateway-key", "ticket": "wq2278",
            "rule": "grant:gateway_admin", "reason": "ok", "result": result,
            "elapsed_s": 0.01}


def test_local_env_uses_minted_key_from_handoff_file(tmp_path, monkeypatch):
    handoff = tmp_path / "wq2278.key"
    handoff.write_text(MINTED)
    sock, seen = fake_broker(tmp_path, [ok_frame(
        {"key_id": "te-wq2278", "expires": "2026-09-21T00:00:00Z",
         "key_file": str(handoff)})])
    _setup(tmp_path, monkeypatch, sock_path=sock)
    env = fworker.build_env("w1", "deepseek-v4-flash", None, str(tmp_path / "wt"), 2278)
    assert env["ANTHROPIC_AUTH_TOKEN"] == MINTED
    # the broker request carried grant REFERENCES, never a key
    assert seen[0]["verb"] == "gateway-key"
    assert seen[0]["argv"][0] == "mint"
    assert MINTED not in json.dumps(seen[0])
    # the self-issued grant is broker-readable (0444) and gateway_admin-shaped
    grant = json.load(open(seen[0]["grant_file"]))
    assert grant["grants"] == [{"kind": "gateway_admin", "node": "gateway:litellm"}]
    assert os.stat(seen[0]["grant_file"]).st_mode & 0o777 == 0o444


def test_broker_down_falls_back_to_static_key(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    env = fworker.build_env("w1", "deepseek-v4-flash", None, str(tmp_path / "wt"), 2278)
    assert env["ANTHROPIC_AUTH_TOKEN"] == STATIC


def test_broker_deny_falls_back_to_static_key(tmp_path, monkeypatch):
    sock, _ = fake_broker(tmp_path, [{
        "ok": False, "verb": "gateway-key", "ticket": "wq2278",
        "rule": "grant_no_match", "reason": "no grant", "result": {},
        "elapsed_s": 0.01}])
    _setup(tmp_path, monkeypatch, sock_path=sock)
    env = fworker.build_env("w1", "deepseek-v4-flash", None, str(tmp_path / "wt"), 2278)
    assert env["ANTHROPIC_AUTH_TOKEN"] == STATIC


def test_revoke_is_best_effort_and_never_raises(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)  # no broker socket at all
    fworker.revoke_ticket_gateway_key(2278)  # must not raise


def test_pop_then_set_exactly_one_cloud_pool(tmp_path, monkeypatch):
    """T10(e): a polluted parent env is scrubbed and the CLOUD pool ends with
    EXACTLY one credential var — the static OAuth bearer, which stays in env
    because no provider-side short-lived option exists (design §7 rank 6)."""
    for var in CRED_VARS:
        monkeypatch.setenv(var, "stale-" + var)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://stale")
    _setup(tmp_path, monkeypatch)
    env = fworker.build_env("w1", "claude-sonnet-5", "high", str(tmp_path / "wt"), 2278)
    present = [v for v in CRED_VARS if v in env]
    assert present == ["CLAUDE_CODE_OAUTH_TOKEN"]
    assert "ANTHROPIC_BASE_URL" not in env


def test_pop_then_set_exactly_one_local_pool(tmp_path, monkeypatch):
    for var in CRED_VARS:
        monkeypatch.setenv(var, "stale-" + var)
    _setup(tmp_path, monkeypatch)
    env = fworker.build_env("w1", "deepseek-v4-flash", None, str(tmp_path / "wt"), 2278)
    present = [v for v in CRED_VARS if v in env]
    assert present == ["ANTHROPIC_AUTH_TOKEN"]
    assert env["ANTHROPIC_AUTH_TOKEN"] not in ("stale-ANTHROPIC_AUTH_TOKEN",)
