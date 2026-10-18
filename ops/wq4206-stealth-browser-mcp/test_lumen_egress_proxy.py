import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lumen_egress_proxy as egress  # noqa: E402
import lumen_stealth_guard as guard  # noqa: E402

SENTINEL = b"wq4290-loopback-sentinel"


@pytest.fixture(scope="module")
def proxy():
    host, port = egress.start().removeprefix("http://").split(":")
    return host, int(port)


@pytest.fixture
def sentinel():
    """A loopback HTTP server standing in for a private fleet service; records each hit."""
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.path, dict(self.headers)))
            self.send_response(200)
            self.send_header("Content-Length", str(len(SENTINEL)))
            self.end_headers()
            self.wfile.write(SENTINEL)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_port, hits
    server.shutdown()


def exchange(proxy, request):
    with socket.create_connection(proxy, timeout=10) as sock:
        sock.sendall(request)
        chunks = []
        while data := sock.recv(65536):
            chunks.append(data)
    return b"".join(chunks)


@pytest.mark.parametrize("target", ["127.0.0.1", "localhost", "[::1]", "2130706433", "0x7f.1", "10.0.0.5",
                                    "169.254.169.254", "100.117.66.102"])
def test_private_targets_are_refused_before_any_connection(proxy, sentinel, target):
    port, hits = sentinel
    for request in (f"GET http://{target}:{port}/ HTTP/1.1\r\nHost: {target}\r\n\r\n",
                    f"CONNECT {target}:{port} HTTP/1.1\r\nHost: {target}:{port}\r\n\r\n"):
        reply = exchange(proxy, request.encode())
        assert reply.startswith(b"HTTP/1.1 403"), reply[:200]
        assert SENTINEL not in reply
    assert hits == []


@pytest.mark.parametrize("request_line", [b"GET /relative HTTP/1.1", b"GET file:///etc/passwd HTTP/1.1",
                                          b"GET ftp://8.8.8.8/ HTTP/1.1", b"CONNECT 8.8.8.8 HTTP/1.1"])
def test_malformed_or_non_http_requests_are_refused(proxy, request_line):
    assert exchange(proxy, request_line + b"\r\n\r\n").startswith(b"HTTP/1.1 403")


def test_forwards_to_the_checked_address_not_a_fresh_lookup(proxy, sentinel, monkeypatch):
    # public.example stands for a public host whose checked address is the sentinel.
    # The proxy must connect to what public_addresses returned (here the sentinel), not
    # re-resolve the name: that pinning is what defeats DNS rebinding.
    port, hits = sentinel
    monkeypatch.setattr(guard, "public_addresses", lambda host: ["127.0.0.1"] if host == "public.example" else [])
    reply = exchange(proxy, (f"GET http://public.example:{port}/p?q=1 HTTP/1.1\r\nHost: public.example:{port}\r\n"
                             "Proxy-Connection: keep-alive\r\nConnection: keep-alive\r\n\r\n").encode())
    assert reply.startswith(b"HTTP/1.0 200") and reply.endswith(SENTINEL), reply[:200]
    path, headers = hits[0]
    assert path == "/p?q=1"
    assert "Proxy-Connection" not in headers and headers["Connection"] == "close"


def test_connect_tunnels_to_the_checked_address(proxy, sentinel, monkeypatch):
    port, hits = sentinel
    monkeypatch.setattr(guard, "public_addresses", lambda host: ["127.0.0.1"])
    reply = exchange(proxy, (f"CONNECT public.example:{port} HTTP/1.1\r\n\r\n"
                             f"GET /tunnel HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n").encode())
    assert reply.startswith(b"HTTP/1.1 200 Connection Established") and reply.endswith(SENTINEL), reply[:200]
    assert hits[0][0] == "/tunnel"


def test_rewrite_head():
    out = egress.rewrite_head("POST", "/x", "HTTP/1.1",
                              b"Host: a\r\nProxy-Authorization: Basic zz\r\nKeep-Alive: 5\r\nContent-Length: 2")
    assert out == b"POST /x HTTP/1.1\r\nHost: a\r\nContent-Length: 2\r\nConnection: close\r\n\r\n"
