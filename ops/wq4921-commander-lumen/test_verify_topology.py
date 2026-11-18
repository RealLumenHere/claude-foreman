"""Tests for verify_topology (wq#4921). No systemd, no live tunnel: systemctl and the
metrics fetch are injected; the gateway check runs against a real local HTTP server."""

import http.server
import importlib.util
import json
import threading
import urllib.error
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "verify_topology", Path(__file__).resolve().parent / "verify_topology.py")
vt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vt)

METRICS_OK = "# HELP x\ncloudflared_tunnel_ha_connections 4\ncloudflared_tunnel_total_requests 9\n"


def systemctl_all_up(verb, unit):
    return {"is-active": "active", "is-enabled": "enabled"}[verb]


class _Handler(http.server.BaseHTTPRequestHandler):
    reply = b'{"jsonrpc":"2.0","id":1,"result":{}}'

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.reply)

    def log_message(self, *a):
        pass


@pytest.fixture
def gateway():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/mcp"
    srv.shutdown()


def _main(argv, run=systemctl_all_up, metrics=METRICS_OK):
    return vt.main(argv, run=run, get=lambda url, t: metrics)


def test_all_healthy_exits_zero(gateway, capsys):
    assert _main(["--gateway-url", gateway, "--min-conns", "4"]) == 0
    assert "FAIL" not in capsys.readouterr().out


def test_masked_unit_fails(gateway):
    def run(verb, unit):
        if unit == "cloudflared-mcp.service":
            return {"is-active": "inactive", "is-enabled": "masked"}[verb]
        return systemctl_all_up(verb, unit)

    assert _main(["--gateway-url", gateway], run=run) == 1


def test_active_but_disabled_unit_fails(gateway):
    def run(verb, unit):
        return "disabled" if verb == "is-enabled" else "active"

    assert _main(["--gateway-url", gateway], run=run) == 1


def test_too_few_edge_connections_fails(gateway):
    metrics = METRICS_OK.replace("connections 4", "connections 2")
    assert _main(["--gateway-url", gateway, "--min-conns", "4"], metrics=metrics) == 1
    assert _main(["--gateway-url", gateway, "--min-conns", "2"], metrics=metrics) == 0


def test_metric_absent_or_unreachable_fails(gateway):
    assert _main(["--gateway-url", gateway], metrics="other_metric 1\n") == 1

    def boom(url, t):
        raise urllib.error.URLError("refused")

    assert vt.main(["--gateway-url", gateway], run=systemctl_all_up, get=boom) == 1


def test_dead_gateway_fails():
    assert _main(["--gateway-url", "http://127.0.0.1:9/mcp", "--timeout", "2"]) == 1


def test_gateway_without_result_fails(gateway, monkeypatch):
    monkeypatch.setattr(_Handler, "reply", b'{"jsonrpc":"2.0","id":1,"error":{}}')
    assert _main(["--gateway-url", gateway]) == 1


def test_json_output(gateway, capsys):
    _main(["--gateway-url", gateway, "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is True
    assert [c["name"] for c in doc["checks"]] == [
        "unit:dc-gateway.service", "unit:cloudflared-mcp.service",
        "tunnel:ha_connections", "gateway:mcp"]
