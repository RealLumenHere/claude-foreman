"""dashboard.py — HTTP layer tests (wq#718 AUTO-6).

render_html() is tested as a pure function against a synthetic snapshot (no server, no
dependency on dashboard_data's real data sources). A short-lived live-server test then
proves the two guardrails that actually matter for a "never a control surface" claim:
GET works, and every mutating HTTP verb (POST/PUT/DELETE) is rejected — BaseHTTPRequest
Handler's stdlib default (501) applies automatically because dashboard.py defines no
do_POST/do_PUT/do_DELETE at all, which is the point: there is no code path to add one to
by accident.
"""
import json
import http.client
import os
import sys
import threading
import time

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, DISPATCHER_DIR)
import dashboard         # noqa: E402
import dashboard_data    # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

SAMPLE_SNAPSHOT = {
    "generated_at": "2026-08-22T00:00:00+00:00",
    "running": {"workers": [{"ticket": 1, "unit": "fworker-wq1", "lane": "testlane",
                             "model": "claude-sonnet-5", "started_at": 100.0,
                             "elapsed_s": 60.0, "projected_end": 3700.0}]},
    "queue_depth": {"testlane": {"open": 2, "eligible": 1, "blocked": 0}},
    "failure_rate": {"overall": {"24h": {"total": 4, "failures": 1, "rate": 0.25},
                                 "7d": {"total": 10, "failures": 3, "rate": 0.3}}},
    "circuit_breaker": {"armed": True, "tripped_now": False, "total": 4, "failures": 1,
                        "rate": 0.25, "window_minutes": 90},
    "stuck_tickets": {},
    "budget": {"stop_active": False},
}


def test_render_html_includes_key_sections():
    html = dashboard.render_html(SAMPLE_SNAPSHOT)
    assert "wq1" in html and "testlane" in html
    assert "ARMED" in html
    assert "25%" in html  # 24h failure rate


def test_render_html_disarmed_label():
    snap = dict(SAMPLE_SNAPSHOT, circuit_breaker=dict(SAMPLE_SNAPSHOT["circuit_breaker"],
                                                       armed=False))
    html = dashboard.render_html(snap)
    assert "DISARMED" in html


@pytest.fixture
def live_server(monkeypatch):
    monkeypatch.setattr(dashboard_data, "snapshot", lambda: SAMPLE_SNAPSHOT)
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    port = server.server_address[1]
    try:
        yield port
    finally:
        server.shutdown()
        t.join(timeout=5)


def test_get_root_returns_html(live_server):
    conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
    conn.request("GET", "/")
    resp = conn.getresponse()
    assert resp.status == 200
    assert "text/html" in resp.getheader("Content-Type")
    resp.read()


def test_get_api_snapshot_returns_json(live_server):
    conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
    conn.request("GET", "/api/snapshot")
    resp = conn.getresponse()
    body = json.loads(resp.read())
    assert resp.status == 200
    assert body == SAMPLE_SNAPSHOT


def test_unknown_path_404(live_server):
    conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
    conn.request("GET", "/nope")
    resp = conn.getresponse()
    assert resp.status == 404
    resp.read()


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_mutating_verbs_are_rejected(live_server, method):
    """No do_POST/do_PUT/do_DELETE exists — BaseHTTPRequestHandler's stdlib default
    (501 Unsupported method) applies. This is the guardrail check: there is no code
    path here that could ever act on a mutating request."""
    conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
    conn.request(method, "/api/snapshot")
    resp = conn.getresponse()
    assert resp.status == 501
    resp.read()
