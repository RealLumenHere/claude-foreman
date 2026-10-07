"""CLASS 7 (ollama keep_alive pin, wq#3558) with injected /api/ps and expire."""
import datetime
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import selfheal_sweep
import sh_ollama

NOW = 1_000_000.0
HOST = "http://127.0.0.1:11434"


def iso(epoch):
    # ollama's own shape: nanosecond fraction, Z suffix
    dt = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "226567022Z"


def ps(*models):
    return lambda host: [{"name": n, "expires_at": iso(NOW + ttl)} for n, ttl in models]


def test_parse_expires_handles_nanoseconds_and_offsets():
    assert sh_ollama.parse_expires("2026-10-07T11:28:58.226567022Z") == \
        datetime.datetime(2026, 10, 7, 11, 28, 58, 226567,
                          tzinfo=datetime.timezone.utc).timestamp()
    assert sh_ollama.parse_expires("2026-10-07T04:28:58-07:00") == \
        sh_ollama.parse_expires("2026-10-07T11:28:58Z")
    assert sh_ollama.parse_expires("") is None
    assert sh_ollama.parse_expires("garbage") is None


def test_pinned_model_is_expired_and_unpinned_one_left_alone(make_ctx):
    # the 2026-10-07 wedge: nomic pinned ~30 min by minerva's keep_alive '30m'
    calls = []
    ctx = make_ctx({}, now=NOW)
    sh_ollama.sweep(ctx, ps_fn=ps(("nomic-embed-text:latest", 1800), ("lfm2:1b", 25)),
                    expire_fn=lambda h, m: calls.append((h, m)) or True)
    assert calls == [(HOST, "nomic-embed-text:latest")]
    [(key, title)] = ctx.out.alerts
    assert key == f"ollama-pin-broken:{HOST}:nomic-embed-text:latest"
    assert "sent keep_alive:0" in title
    assert ctx.out.tickets == []


def test_policy_ttl_never_triggers(make_ctx):
    calls = []
    ctx = make_ctx({"ollama": {"max_ttl_s": 120}}, now=NOW)
    sh_ollama.sweep(ctx, ps_fn=ps(("gemma4", 30), ("nomic-embed-text:latest", 119)),
                    expire_fn=lambda h, m: calls.append(m) or True)
    assert calls == [] and ctx.actions == [] and ctx.state["ollama_pin_streak"] == {}


def test_dry_run_touches_nothing(make_ctx):
    calls = []
    ctx = make_ctx({}, now=NOW, dry_run=True)
    sh_ollama.sweep(ctx, ps_fn=ps(("nomic-embed-text:latest", 1800)),
                    expire_fn=lambda h, m: calls.append(m) or True)
    assert calls == [] and ctx.out.alerts == []
    assert any(a.startswith("[ollama] would expire nomic-embed-text:latest") for a in ctx.actions)
    assert ctx.state["ollama_pin_streak"] == {}


def test_repinned_every_sweep_tickets_once_keeps_breaking_and_resolves(make_ctx):
    calls = []
    cfg = {"ollama": {"ticket_after": 2}}
    pinned = ps(("nomic-embed-text:latest", 1800))
    state = {}
    for i in range(4):
        ctx = make_ctx(cfg, state, now=NOW + 60 * i)
        sh_ollama.sweep(ctx, ps_fn=pinned, expire_fn=lambda h, m: calls.append(m) or True)
        state = ctx.state
    assert len(calls) == 4                 # the pin is broken on every sweep
    assert len(ctx.out.tickets) == 0       # last ctx: already filed, deduped
    assert list(state["tickets"]) == [f"ollama-pin-stuck:{HOST} nomic-embed-text:latest"]
    ctx = make_ctx(cfg, state, now=NOW + 300)
    sh_ollama.sweep(ctx, ps_fn=lambda h: [], expire_fn=lambda h, m: True)
    assert state["tickets"] == {} and state["ollama_pin_streak"] == {}


def test_unanswering_host_is_skipped(make_ctx):
    ctx = make_ctx({"ollama": {"hosts": [HOST, "http://10.0.0.9:11434"]}}, now=NOW)
    seen = []
    sh_ollama.sweep(ctx, ps_fn=lambda h: None if "10.0.0.9" in h else [],
                    expire_fn=lambda h, m: seen.append(m))
    assert seen == [] and ctx.out.tickets == []
    assert any("10.0.0.9" in a and "did not answer" in a for a in ctx.actions)


def test_real_http_path_sends_ollamas_unload_call(make_ctx):
    """fetch_ps + expire over a real socket: the unload body is exactly
    {model, keep_alive: 0} with no prompt, the shape GenerateHandler routes to
    expireRunner (ollama v0.34.4 server/routes.go:424)."""
    posts = []
    body_ps = json.dumps({"models": [{"name": "nomic-embed-text:latest",
                                      "expires_at": iso(NOW + 1800)}]}).encode()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body_ps if self.path == "/api/ps" else b"{}")

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            posts.append((self.path, json.loads(self.rfile.read(n))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"done": true}')

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        host = f"http://127.0.0.1:{srv.server_port}"
        ctx = make_ctx({"ollama": {"hosts": [host]}}, now=NOW)
        sh_ollama.sweep(ctx)
    finally:
        srv.shutdown()
    assert posts == [("/api/generate", {"model": "nomic-embed-text:latest", "keep_alive": 0})]


def test_class_is_registered_in_the_sweep():
    assert selfheal_sweep.CLASSES["ollama"] is sh_ollama
