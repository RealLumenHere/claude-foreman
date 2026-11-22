#!/usr/bin/env python3
"""wq#5239 — trivy weekly scan.sh: ntfy password off curl argv; unit timeout covers 2x45m.

The ntfy_send function is lifted out of the bundle's scan.sh (env-file path and ntfy URL
rewritten to temp fixtures), then run under bash with a curl shim on PATH that records
its argv before exec'ing the real curl. A local HTTP server captures the Authorization
header, so the test proves both halves: the password is on no argv, and the stdin
config (-K -) still delivers it byte-exact, including the characters curl's config
syntax needs escaped (backslash, double quote) and an '=' that `cut -f2` used to drop.

Run: python3 -m pytest ops/tests/test_wq5239_trivy_ntfy_argv.py -q
"""
import base64
import http.server
import os
import re
import shutil
import subprocess
import threading

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
BUNDLE = os.path.join(REPO, "ops", "wq858-trivy-weekly-scan")
SCAN = os.path.join(BUNDLE, "scan.sh")
UNIT = os.path.join(BUNDLE, "lumen-trivy-weekly.service")
LIVE_ENV = "/home/lumen/agent/.env"
LIVE_URL = "http://127.0.0.1:8080/$NTFY_TOPIC"


def _ntfy_send_src():
    src = open(SCAN).read()
    m = re.search(r"^ntfy_send\(\) \{.*?^\}\n", src, re.S | re.M)
    assert m, "ntfy_send() not found in scan.sh"
    return m.group(0)


class _Capture(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode()
        _Capture.seen.append({"auth": self.headers.get("Authorization"),
                              "title": self.headers.get("Title"), "body": body})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    _Capture.seen = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Capture)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


def _run_send(tmp_path, server, env_line):
    real_curl = shutil.which("curl")
    assert real_curl, "curl not on PATH"
    envf = tmp_path / "agent.env"
    envf.write_text("OTHER=1\n" + env_line + "\n")
    argv_log = tmp_path / "curl.argv"
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "curl"
    shim.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "' + str(argv_log) + '"\n'
                    'exec "' + real_curl + '" "$@"\n')
    os.chmod(shim, 0o755)
    url = "http://127.0.0.1:%d/$NTFY_TOPIC" % server.server_address[1]
    fn = _ntfy_send_src()
    assert LIVE_ENV in fn and LIVE_URL in fn
    fn = fn.replace(LIVE_ENV, str(envf)).replace(LIVE_URL, url)
    script = ("set -euo pipefail\nNTFY_TOPIC=lumen-fleet\n" + fn +
              'ntfy_send "T1" high warning "body one"\n')
    env = dict(os.environ, PATH=str(shim_dir) + os.pathsep + os.environ.get("PATH", ""))
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env,
                       timeout=60)
    return r, argv_log.read_text() if argv_log.exists() else ""


def test_password_delivered_via_stdin_not_argv(tmp_path, server):
    # Built at runtime: backslash, double quote, '=' and a space, all of which the old
    # `cut -d= -f2` / naive config quoting would mangle.
    pw = "pw" + "\\" + "q" + '"' + "x=y" + " z" + "7" * 6
    r, argv = _run_send(tmp_path, server, "NTFY_" + "PASS=" + pw)
    assert r.returncode == 0, r.stderr
    assert argv, "curl shim never ran"
    assert pw not in argv
    assert "7" * 6 not in argv            # no fragment of it either
    assert "-K" in argv.split("\n") and "-u" not in argv.split("\n")
    assert len(_Capture.seen) == 1
    got = _Capture.seen[0]
    assert got["auth"] == "Basic " + base64.b64encode(("lumen:" + pw).encode()).decode()
    assert got["title"] == "T1" and got["body"] == "body one"


def test_missing_password_line_does_not_abort_under_set_e(tmp_path, server):
    r, argv = _run_send(tmp_path, server, "UNRELATED=1")
    assert r.returncode == 0, r.stderr
    assert len(_Capture.seen) == 1        # still posts (server decides on auth)


def test_scan_sh_has_no_curl_user_flag():
    src = open(SCAN).read()
    assert not re.search(r"curl[^\n]*\s-u\s", src)
    assert "NTFY_PASS" not in src.replace(_ntfy_send_src(), "")


def _seconds(span):
    mult = {"": 1, "s": 1, "min": 60, "m": 60, "h": 3600}
    m = re.fullmatch(r"(\d+)\s*(s|min|m|h|)", span.strip())
    assert m, span
    return int(m.group(1)) * mult[m.group(2)]


def test_unit_timeout_exceeds_both_rootfs_walks():
    unit = open(UNIT).read()
    m = re.search(r"^TimeoutStartSec=(.+)$", unit, re.M)
    assert m
    caps = re.findall(r"rootfs [^\n]*--timeout (\d+)m", open(SCAN).read())
    assert len(caps) == 2
    need = sum(int(c) * 60 for c in caps)
    assert _seconds(m.group(1)) > need + 1800  # plus image scans + DB update
