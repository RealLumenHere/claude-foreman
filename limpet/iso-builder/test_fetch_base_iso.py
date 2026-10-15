import hashlib
import http.server
import subprocess
import threading
from pathlib import Path

SCRIPT = Path(__file__).parent / "fetch_base_iso.sh"
VERSION = "99.99"
DESKTOP_ISO = f"xubuntu-{VERSION}-desktop-amd64.iso"
MINIMAL_ISO = f"xubuntu-{VERSION}-minimal-amd64.iso"


class _Server:
    """Threaded HTTP server serving a dict of path -> (status, body)."""

    def __init__(self, routes):
        handler = self._make_handler(routes)
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @staticmethod
    def _make_handler(routes):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = routes.get(self.path, (404, b"not found"))
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        return Handler

    def __enter__(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def _run(base_url, dest):
    return subprocess.run(
        ["bash", str(SCRIPT), VERSION, str(dest)],
        env={"LUMEN_FETCH_BASE_URL": base_url, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )


def test_all_404_leaves_no_marker_and_no_empty_artifacts(tmp_path):
    with _Server({}) as base_url:
        result = _run(base_url, tmp_path)
    assert result.returncode != 0
    assert not (tmp_path / ".xubuntu_download_complete").exists()
    assert not (tmp_path / DESKTOP_ISO).exists()


def test_verified_artifacts_write_marker(tmp_path):
    desktop_bytes = b"desktop-iso-bytes"
    minimal_bytes = b"minimal-iso-bytes"
    sums = (
        f"{hashlib.sha256(desktop_bytes).hexdigest()}  {DESKTOP_ISO}\n"
        f"{hashlib.sha256(minimal_bytes).hexdigest()}  {MINIMAL_ISO}\n"
    ).encode()
    routes = {
        f"/{DESKTOP_ISO}": (200, desktop_bytes),
        f"/{MINIMAL_ISO}": (200, minimal_bytes),
        "/SHA256SUMS": (200, sums),
    }
    with _Server(routes) as base_url:
        result = _run(base_url, tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".xubuntu_download_complete").exists()
    assert (tmp_path / DESKTOP_ISO).read_bytes() == desktop_bytes


def test_checksum_mismatch_leaves_no_marker(tmp_path):
    desktop_bytes = b"desktop-iso-bytes"
    minimal_bytes = b"minimal-iso-bytes"
    wrong_sums = (
        f"{'0' * 64}  {DESKTOP_ISO}\n"
        f"{hashlib.sha256(minimal_bytes).hexdigest()}  {MINIMAL_ISO}\n"
    ).encode()
    routes = {
        f"/{DESKTOP_ISO}": (200, desktop_bytes),
        f"/{MINIMAL_ISO}": (200, minimal_bytes),
        "/SHA256SUMS": (200, wrong_sums),
    }
    with _Server(routes) as base_url:
        result = _run(base_url, tmp_path)
    assert result.returncode != 0
    assert not (tmp_path / ".xubuntu_download_complete").exists()
